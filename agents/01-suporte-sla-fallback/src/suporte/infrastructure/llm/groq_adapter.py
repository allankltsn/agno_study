"""Adapter do provider **Groq** — envolve a Agno e implementa `LLMPort`.

Este módulo é análogo ao `openai_adapter.py`: é um dos poucos lugares onde tipos
da Agno existem (a Agno é confinada à camada `infrastructure`, RF/Req 12.6). O
`GroqAdapter` "veste" um `agno.agent.Agent` configurado com o modelo
`agno.models.groq.Groq` e o expõe pela abstração de domínio `LLMPort`, de modo
que o caso de uso e o `FallbackLLM` nunca conheçam a Agno nem o SDK da Groq.

Princípios aplicados:
- **DIP + LSP**: implementa exatamente o mesmo contrato (`LLMPort`) e a mesma
  semântica de erro (`ProviderUnavailable`) do `OpenAIAdapter`. Trocar um pelo
  outro na `provider_order` não altera o comportamento observável do sistema —
  é apenas mais uma `LLMPort` na lista do `FallbackLLM` (Composite + Strategy).
- **Import tardio da Agno**: `agno` é importado *dentro* dos métodos (não no topo
  do módulo). Isso mantém o domínio/aplicação livres de Agno mesmo por transição
  de import e permite testar o adapter injetando um duplo (`agent_factory`) sem a
  Agno instalada nem acesso à rede (Req 17.2).
- **Segredos fora do código (Req 10)**: a chave de API nunca é passada como
  argumento nem logada; a Agno/SDK a lê do ambiente (ex.: ``GROQ_API_KEY``) em
  tempo de execução.

Notas sobre a API da Agno v2 (premissas):
- `agno.agent.Agent(model=..., instructions=..., context=...)` monta o agente.
- `agent.run(prompt)` retorna um objeto de resposta (`RunOutput`) com o texto em
  `.content` e métricas de tokens em `.metrics` (chaves ``input_tokens`` /
  ``output_tokens``, com aliases tolerados). Essas contagens são **opcionais**:
  quando ausentes, caem para zero (Req 8.2).
- `agent.run(prompt, stream=True)` retorna um iterável de eventos; extraímos o
  delta de texto de cada evento (`.content`) na ordem de emissão.
Como a superfície exata da Agno v2 pode variar por versão, a extração é
defensiva: usa `getattr`/acesso tolerante e nunca quebra o contrato de `Answer`.

Compatibilidade: `from __future__ import annotations` + tipos de `typing` para
Python 3.9+.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer

# Nome do provider — usado em `Answer.provider`, no log estruturado e na lista de
# `attempts` de `ProviderUnavailable`. Deve casar com a chave em `provider_order`.
PROVIDER_NAME = "groq"

# Marcador (sentinel) que o modelo é instruído a emitir quando *não* encontra
# fundamentação na documentação fornecida. O adapter detecta esse marcador na
# resposta e converte para `out_of_scope=True` (RF-03 / Req 3.1), suprimindo
# qualquer texto não fundamentado antes de devolver ao domínio. Manter o marcador
# como uma constante evita "strings mágicas" e facilita o teste.
OUT_OF_SCOPE_MARKER = "[[OUT_OF_SCOPE]]"


class GroqAdapter:
    """Implementa `LLMPort` envolvendo um `Agent` da Agno com o modelo Groq.

    É estruturalmente idêntico ao `OpenAIAdapter` (LSP): mesma assinatura de
    construtor, mesma conversão da resposta da Agno em `Answer`, mesma marcação de
    `out_of_scope` e mesma tradução de falhas em `ProviderUnavailable`. A única
    diferença é o modelo Agno usado (`agno.models.groq.Groq`).

    O agente é construído de forma *preguiçosa* (lazy) na primeira chamada e
    memorizado, para que instanciar o adapter (no `composition_root`) não force o
    import da Agno nem a resolução de credenciais antes do primeiro uso.
    """

    def __init__(
        self,
        model_id: str,
        instructions: List[str],
        product_context: Dict[str, Any],
        *,
        agent_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Configura o adapter (sem montar o `Agent` ainda).

        Args:
            model_id: Id do modelo Groq (ex.: ``"llama-3.3-70b-versatile"``).
            instructions: Linhas de instrução da persona/regras injetadas no
                agente. É aqui (junto de `product_context`) que a documentação do
                produto entra, sem RAG (RF-01).
            product_context: Dados de contexto do produto passados ao agente via
                `context` da Agno (sem RAG).
            agent_factory: **Apenas para teste.** Se fornecido, é chamado no lugar
                da construção real do `Agent` da Agno, permitindo injetar um duplo
                sem rede (Req 17.2). Em produção fica ``None`` e o adapter monta o
                `Agent` real com import tardio da Agno.
        """
        self._model_id = model_id
        self._instructions = list(instructions)
        self._product_context = dict(product_context)
        self._agent_factory = agent_factory
        # Cache do agente montado (lazy). None até a primeira chamada.
        self._agent: Optional[Any] = None

    # -- Construção do agente (import tardio da Agno) -------------------------

    def _build_agent(self) -> Any:
        """Monta (ou obtém do teste) o `Agent` da Agno com o modelo Groq.

        Importa a Agno **aqui dentro** para manter o import fora do topo do módulo
        (domínio/aplicação nunca importam Agno por transição). Quando um
        `agent_factory` foi injetado, delega a ele — caminho usado pelos testes.

        Returns:
            Uma instância de `agno.agent.Agent` (ou o duplo de teste equivalente).
        """
        if self._agent_factory is not None:
            # Caminho de teste: duplo injetado, sem Agno nem rede.
            return self._agent_factory()

        # Import tardio: a Agno só é carregada quando o adapter é de fato usado.
        from agno.agent import Agent
        from agno.models.groq import Groq

        return Agent(
            model=Groq(id=self._model_id),
            instructions=self._instructions,
            dependencies=self._product_context,
            add_dependencies_to_context=True,
        )

    def _get_agent(self) -> Any:
        """Devolve o `Agent` memorizado, construindo-o na primeira chamada."""
        if self._agent is None:
            self._agent = self._build_agent()
        return self._agent

    # -- Contrato LLMPort -----------------------------------------------------

    def ask(self, prompt: str) -> Answer:
        """Modo *run*: retorna a resposta completa convertida em `Answer`.

        Cronometra a chamada (para `latency_ms`), delega ao `Agent` da Agno,
        extrai texto e tokens de forma tolerante e marca `out_of_scope` quando o
        modelo emite o marcador de falta de fundamentação (RF-03).

        Args:
            prompt: A pergunta/prompt já montado.

        Returns:
            Um `Answer` com `provider="groq"`, tokens (>= 0), `latency_ms` e o
            `out_of_scope` conforme o sinal do modelo.

        Raises:
            ProviderUnavailable: Se a chamada ao provider falhar (rede, timeout,
                autenticação, etc.). Traduz qualquer erro do SDK/Agno para o
                contrato de erro do domínio (LSP), com ``attempts=["groq"]``.
        """
        started = time.monotonic()
        try:
            agent = self._get_agent()
            response = agent.run(prompt)
        except ProviderUnavailable:
            # Já é o erro de domínio esperado; não reembrulha.
            raise
        except Exception as exc:  # noqa: BLE001 — tradução deliberada p/ o domínio
            # LSP: qualquer falha de infraestrutura vira o mesmo contrato de erro
            # dos demais adapters, sem vazar tipos/stacktrace do SDK ao domínio.
            raise ProviderUnavailable(
                attempts=[PROVIDER_NAME],
                message=f"Provider '{PROVIDER_NAME}' indisponível: {exc}",
            ) from exc

        latency_ms = (time.monotonic() - started) * 1000.0
        return self._to_answer(response, latency_ms)

    def ask_stream(self, prompt: str) -> Iterator[str]:
        """Modo *stream*: gera fragmentos de texto na ordem de emissão.

        A concatenação dos fragmentos é consistente em conteúdo com `ask(prompt).
        text` para o mesmo prompt/configuração (LSP entre modos, Req 2.5). Como um
        gerador, a tradução de erro para `ProviderUnavailable` acontece durante a
        iteração.

        Args:
            prompt: A pergunta/prompt já montado.

        Yields:
            Pedaços de texto (`str`) na ordem em que a Agno os emite.

        Raises:
            ProviderUnavailable: Se a chamada de streaming ao provider falhar.
        """
        try:
            agent = self._get_agent()
            stream = agent.run(prompt, stream=True)
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(
                attempts=[PROVIDER_NAME],
                message=f"Provider '{PROVIDER_NAME}' indisponível: {exc}",
            ) from exc

        try:
            for event in stream:
                chunk = self._extract_chunk_text(event)
                if chunk:
                    yield chunk
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(
                attempts=[PROVIDER_NAME],
                message=f"Provider '{PROVIDER_NAME}' indisponível: {exc}",
            ) from exc

    # -- Conversão da resposta da Agno em Answer ------------------------------

    def _to_answer(self, response: Any, latency_ms: float) -> Answer:
        """Converte a resposta da Agno em um `Answer` de domínio.

        Extrai texto e tokens de forma defensiva (a superfície da Agno v2 pode
        variar), garante tokens >= 0 (zero quando omitidos, Req 8.1/8.2) e detecta
        o marcador de fora de escopo, suprimindo o texto não fundamentado nesse
        caso (RF-03).

        Args:
            response: Objeto de resposta retornado por `agent.run`.
            latency_ms: Latência medida da chamada, em milissegundos.

        Returns:
            Um `Answer` imutável com os metadados de custo/observabilidade.
        """
        raw_text = self._extract_text(response)
        prompt_tokens, completion_tokens = self._extract_tokens(response)

        out_of_scope = OUT_OF_SCOPE_MARKER in raw_text
        # RF-03: quando fora de escopo, não devolvemos o texto não fundamentado;
        # o caso de uso substitui por uma mensagem de encaminhamento a humano.
        text = "" if out_of_scope else raw_text

        return Answer(
            text=text,
            provider=PROVIDER_NAME,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            out_of_scope=out_of_scope,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Obtém o texto da resposta de forma tolerante à versão da Agno.

        Tenta `.content` (RunOutput da Agno v2); recai para `.text` ou para a
        conversão em `str` como último recurso.
        """
        content = getattr(response, "content", None)
        if content is None:
            content = getattr(response, "text", None)
        if content is None:
            content = response if isinstance(response, str) else str(response)
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _extract_chunk_text(event: Any) -> str:
        """Extrai o delta de texto de um evento de streaming da Agno.

        Aceita tanto eventos com `.content` (Agno v2) quanto strings puras;
        ignora eventos sem texto (retorna ``""``), que o chamador descarta.
        """
        if isinstance(event, str):
            return event
        content = getattr(event, "content", None)
        if content is None:
            content = getattr(event, "delta", None)
        if content is None:
            return ""
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _extract_tokens(response: Any) -> "tuple[int, int]":
        """Extrai `(prompt_tokens, completion_tokens)` da resposta da Agno.

        A contagem de tokens é **opcional** (Req 8.2): quando o provider a omite,
        ou quando a estrutura de métricas não é reconhecida, retorna ``(0, 0)``.
        Valores negativos ou não inteiros são normalizados para inteiros >= 0
        (Req 8.1).

        Aceita métricas como dict (``{"input_tokens": ..., "output_tokens": ...}``)
        ou como objeto com atributos, tolerando aliases comuns
        (``prompt_tokens``/``completion_tokens``).
        """
        metrics = getattr(response, "metrics", None)
        if metrics is None:
            return (0, 0)

        prompt_tokens = GroqAdapter._read_metric(
            metrics, ("input_tokens", "prompt_tokens")
        )
        completion_tokens = GroqAdapter._read_metric(
            metrics, ("output_tokens", "completion_tokens")
        )
        return (
            GroqAdapter._coerce_non_negative_int(prompt_tokens),
            GroqAdapter._coerce_non_negative_int(completion_tokens),
        )

    @staticmethod
    def _read_metric(metrics: Any, keys: "tuple[str, ...]") -> Any:
        """Lê a primeira chave/atributo presente em `metrics` dentre `keys`.

        Suporta métricas em forma de `dict` ou objeto. Alguns SDKs devolvem
        contagens como lista (uma por etapa); nesse caso, soma os itens. Retorna
        ``None`` quando nenhuma chave é encontrada.
        """
        for key in keys:
            value: Any = None
            if isinstance(metrics, dict):
                value = metrics.get(key)
            else:
                value = getattr(metrics, key, None)
            if value is None:
                continue
            # Algumas versões acumulam por etapa em lista: soma os valores.
            if isinstance(value, (list, tuple)):
                total = 0
                for item in value:
                    try:
                        total += int(item)
                    except (TypeError, ValueError):
                        continue
                return total
            return value
        return None

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int:
        """Normaliza um valor de token em inteiro >= 0 (Req 8.1/8.2).

        Trata ``None``, tipos inesperados e negativos convertendo/limitando para
        zero, garantindo o invariante do `Answer` sem quebrar o contrato.
        """
        if value is None:
            return 0
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return 0
        return coerced if coerced > 0 else 0
