"""Adapter do provider OpenAI (envolve a Agno) — implementa `LLMPort`.

Este é um dos **únicos** lugares onde tipos da Agno existem (a outra é o
`groq_adapter.py`/`ollama_adapter.py`). O domínio e a aplicação nunca conhecem a
Agno: dependem apenas da abstração `LLMPort` e do value object `Answer` (DIP).

Pontos de estudo materializados aqui:

- **Agno importada *dentro* dos métodos/`__init__`**, nunca no topo do módulo. Isso
  mantém o módulo *importável sem a Agno instalada* (útil para as verificações de
  isolamento de camadas e para importar o pacote em ambientes sem o SDK), e confina os
  tipos Agno estritamente ao momento da construção/execução do adapter.
- **Documentação do produto injetada via `instructions` + `context`** (RF-01), sem RAG:
  o adapter recebe a persona/regras (`instructions`) e um dicionário com a doc do produto
  (`product_context`) e os entrega ao `Agent` da Agno.
- **Detecção de "fora de escopo" (RF-03)**: instruímos o modelo a emitir um marcador
  sentinela (`[OUT_OF_SCOPE]`) quando não conseguir fundamentar a resposta na doc. O
  adapter detecta o marcador, remove-o do texto e marca `Answer.out_of_scope=True` — em
  vez de devolver conteúdo possivelmente alucinado.
- **Tradução de falhas em `ProviderUnavailable` (LSP)**: qualquer erro de rede/timeout/
  autenticação vira `ProviderUnavailable(attempts=["openai"])`, o mesmo contrato de erro
  de todos os adapters, para que o `FallbackLLM` possa tratar todos de maneira uniforme.

Testabilidade (tarefa 7.3): o construtor aceita, opcionalmente, um `agent` já pronto ou
uma `agent_factory` — assim os testes injetam um duplo (fake) no lugar do `Agent` da Agno
e verificam a conversão para `Answer` **sem rede**. Em produção, nada é injetado e o
adapter monta o `Agent` real da Agno.

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para Python 3.9+.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer

# Nome do provider que este adapter representa. Usado em `Answer.provider` e na lista de
# `attempts` de `ProviderUnavailable`.
PROVIDER_NAME = "openai"

# Marcador sentinela que o modelo deve emitir quando não conseguir fundamentar a resposta
# na documentação fornecida (RF-03). Detectá-lo é mais robusto e barato do que tentar
# interpretar texto livre; ao encontrá-lo, removemos o marcador e sinalizamos out_of_scope.
OUT_OF_SCOPE_MARKER = "[OUT_OF_SCOPE]"

# Instrução acrescentada às `instructions` do usuário para ativar o protocolo do marcador.
# É explícita e determinística: o modelo responde SÓ com o marcador quando não puder
# fundamentar a resposta, evitando alucinação (RF-03).
_OUT_OF_SCOPE_INSTRUCTION = (
    "Responda exclusivamente com base na documentação do produto fornecida no contexto. "
    "Se a resposta não estiver fundamentada nessa documentação, responda APENAS com o "
    f"marcador {OUT_OF_SCOPE_MARKER} e nada mais — não invente informação."
)


class OpenAIAdapter:
    """Adapter que envolve um `Agent` da Agno com o modelo OpenAI e implementa `LLMPort`.

    Conforma-se ao `LLMPort` de forma **estrutural** (Protocol/duck typing): expõe `ask` e
    `ask_stream` com as assinaturas esperadas, sem herança explícita.

    Args:
        model_id: Id do modelo OpenAI (ex.: ``"gpt-4o-mini"``), repassado a
            ``OpenAIChat(id=model_id)``.
        instructions: Lista de instruções/persona do agente (RF-01). O adapter acrescenta
            a instrução do protocolo de "fora de escopo" ao final.
        product_context: Dicionário com a documentação do produto a ser injetada como
            `context` do `Agent` (sem RAG).
        agent: (Opcional, para teste) um `Agent` já construído/duplo a ser usado no lugar
            do `Agent` real da Agno. Quando fornecido, a Agno **não** é importada.
        agent_factory: (Opcional, para teste) fábrica ``(model_id, instructions, context)
            -> agent`` usada para construir o agente. Precede a construção real via Agno.

    Note:
        Se nem `agent` nem `agent_factory` forem passados, o `Agent` real da Agno é
        construído no `__init__` (importando a Agno **aqui dentro**). A env var da chave
        (``OPENAI_API_KEY``) é lida pelo próprio SDK a partir do ambiente — o adapter nunca
        manipula nem loga o valor da chave.
    """

    def __init__(
        self,
        model_id: str,
        instructions: List[str],
        product_context: Dict[str, Any],
        *,
        agent: Optional[Any] = None,
        agent_factory: Optional[Callable[[str, List[str], Dict[str, Any]], Any]] = None,
    ) -> None:
        self._model_id = model_id
        # Copiamos e acrescentamos a instrução do protocolo de fora de escopo, sem mutar a
        # lista recebida do chamador (evita efeitos colaterais surpreendentes).
        self._instructions: List[str] = list(instructions) + [_OUT_OF_SCOPE_INSTRUCTION]
        self._product_context = product_context

        if agent is not None:
            # Caminho de teste: usa o duplo injetado diretamente.
            self._agent = agent
        elif agent_factory is not None:
            # Caminho de teste/customização: constrói via fábrica injetada.
            self._agent = agent_factory(
                self._model_id, self._instructions, self._product_context
            )
        else:
            # Caminho de produção: monta o Agent real da Agno.
            self._agent = self._build_agent()

    def _build_agent(self) -> Any:
        """Monta o `Agent` real da Agno com o modelo OpenAI.

        A Agno é importada **aqui dentro** (não no topo do módulo) para preservar a
        importabilidade do pacote sem o SDK e confinar os tipos Agno à infraestrutura.

        Returns:
            Uma instância de ``agno.agent.Agent`` configurada com ``OpenAIChat(id=...)``,
            as `instructions` (persona + protocolo de fora de escopo) e o `context` com a
            doc do produto.

        Raises:
            ProviderUnavailable: Se a construção do `Agent`/modelo falhar (ex.: SDK
                ausente ou erro de configuração), traduzida para o contrato de erro comum.
        """
        try:
            from agno.agent import Agent  # import interno (Agno confinada aqui)
            from agno.models.openai import OpenAIChat

            return Agent(
                model=OpenAIChat(id=self._model_id),
                instructions=self._instructions,
                # Doc do produto injetada como contexto (RF-01, sem RAG). Na Agno
                # v2 a injecao de variaveis estaticas no contexto usa `dependencies`
                # (+ add_dependencies_to_context=True), no lugar do antigo `context=`.
                dependencies=self._product_context,
                add_dependencies_to_context=True,
            )
        except Exception as exc:  # noqa: BLE001 - traduzimos qualquer falha de setup
            # Falha ao montar o provider é, para o resto do sistema, um provider
            # indisponível (mesmo contrato de erro — LSP).
            raise ProviderUnavailable(attempts=[PROVIDER_NAME]) from exc

    def ask(self, prompt: str) -> Answer:
        """Modo *run*: envia o prompt à Agno e converte a resposta em `Answer`.

        Mede a latência ao redor da chamada, extrai os tokens das métricas da Agno (com
        zero quando omitidos e nunca negativos), detecta o marcador de fora de escopo e
        traduz falhas de rede/timeout/autenticação em `ProviderUnavailable`.

        Args:
            prompt: A pergunta/prompt já montado.

        Returns:
            Um `Answer` com `text`, `provider="openai"`, tokens, `latency_ms` e
            `out_of_scope` conforme sinalizado pelo modelo.

        Raises:
            ProviderUnavailable: Em qualquer falha de rede/timeout/autenticação do provider
                (``attempts=["openai"]``).
        """
        start = time.monotonic()
        try:
            run_output = self._agent.run(prompt)
        except Exception as exc:  # noqa: BLE001 - falha de rede/timeout/auth -> contrato comum
            raise ProviderUnavailable(attempts=[PROVIDER_NAME]) from exc

        latency_ms = (time.monotonic() - start) * 1000.0

        raw_text = self._extract_text(run_output)
        text, out_of_scope = self._interpret_scope(raw_text)
        prompt_tokens, completion_tokens = self._extract_tokens(run_output)

        return Answer(
            text=text,
            provider=PROVIDER_NAME,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            out_of_scope=out_of_scope,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        """Modo *stream*: gera fragmentos de texto na ordem de emissão da Agno.

        Chama ``agent.run(prompt, stream=True)`` e repassa o `content` de cada evento na
        ordem recebida. Falhas de rede/timeout/autenticação — na abertura do stream ou
        durante a iteração — são traduzidas em `ProviderUnavailable`.

        Args:
            prompt: A pergunta/prompt já montado.

        Yields:
            Pedaços de texto (`str`) na ordem de emissão. Fragmentos vazios/None são
            ignorados (a Agno pode emitir eventos sem `content`, ex.: eventos de controle).

        Raises:
            ProviderUnavailable: Em qualquer falha de rede/timeout/autenticação do provider
                (``attempts=["openai"]``).
        """
        try:
            stream = self._agent.run(prompt, stream=True)
            for event in stream:
                chunk = getattr(event, "content", None)
                if chunk:
                    yield chunk
        except ProviderUnavailable:
            # Já está no contrato correto; não re-embrulhar.
            raise
        except Exception as exc:  # noqa: BLE001 - falha de rede/timeout/auth -> contrato comum
            raise ProviderUnavailable(attempts=[PROVIDER_NAME]) from exc

    # ------------------------------------------------------------------ helpers ---
    @staticmethod
    def _extract_text(run_output: Any) -> str:
        """Extrai o texto de resposta do `RunOutput` da Agno.

        A Agno v2 expõe o texto em ``RunOutput.content``. Somos tolerantes: se o atributo
        estiver ausente/None, usamos string vazia.

        Args:
            run_output: O objeto retornado por ``agent.run(prompt)``.

        Returns:
            O texto da resposta (possivelmente vazio), sem tratamento de escopo.
        """
        content = getattr(run_output, "content", None)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _interpret_scope(text: str) -> "tuple[str, bool]":
        """Detecta o marcador de fora de escopo e o remove do texto.

        Args:
            text: Texto cru retornado pelo modelo.

        Returns:
            Uma tupla ``(texto_limpo, out_of_scope)``. Quando o marcador está presente,
            ``out_of_scope=True`` e o marcador é removido do texto (deixando o restante,
            já sem espaços nas pontas).
        """
        if OUT_OF_SCOPE_MARKER in text:
            cleaned = text.replace(OUT_OF_SCOPE_MARKER, "").strip()
            return cleaned, True
        return text, False

    @staticmethod
    def _extract_tokens(run_output: Any) -> "tuple[int, int]":
        """Extrai ``(prompt_tokens, completion_tokens)`` das métricas da Agno.

        A Agno v2 expõe as métricas em ``RunOutput.metrics`` com os campos
        ``input_tokens``/``output_tokens`` (herdados de ``BaseMetrics``). Este helper é
        defensivo: se `metrics` ou os campos estiverem ausentes, retorna zero; valores
        negativos ou não inteiros são normalizados para ``>= 0`` (Req: tokens ``>= 0``,
        zero quando omitidos).

        Args:
            run_output: O objeto retornado por ``agent.run(prompt)``.

        Returns:
            Tupla ``(prompt_tokens, completion_tokens)``, ambos inteiros ``>= 0``.
        """
        metrics = getattr(run_output, "metrics", None)
        prompt_tokens = OpenAIAdapter._coerce_non_negative_int(
            getattr(metrics, "input_tokens", 0)
        )
        completion_tokens = OpenAIAdapter._coerce_non_negative_int(
            getattr(metrics, "output_tokens", 0)
        )
        return prompt_tokens, completion_tokens

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int:
        """Converte um valor de token em inteiro ``>= 0``, tolerando None/tipos inválidos.

        Args:
            value: Valor cru vindo das métricas (int, float, None, etc.).

        Returns:
            ``max(int(value), 0)``; ``0`` quando o valor for None ou não conversível.
        """
        if value is None:
            return 0
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0
