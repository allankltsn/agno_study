"""Adapter do provider **Ollama** (envolve a Agno) — implementa `LLMPort`.

Este é um dos **únicos** lugares onde tipos da Agno existem (os outros são o
`openai_adapter.py` e o `groq_adapter.py`). O domínio e a aplicação nunca conhecem
a Agno: dependem apenas da abstração `LLMPort` e do value object `Answer` (DIP).

O `OllamaAdapter` serve como provider de **fallback local** (custo zero, sem rede
externa) no ambiente `docker-compose` (Req 15.1). É estruturalmente idêntico ao
`OpenAIAdapter`/`GroqAdapter` (LSP): mesma conversão da resposta da Agno em
`Answer`, mesma marcação de `out_of_scope` e a mesma tradução de falhas em
`ProviderUnavailable`. A única diferença é o modelo Agno usado
(`agno.models.ollama.Ollama`) e o fato de o endpoint do servidor Ollama vir de
uma variável de ambiente.

Pontos de estudo materializados aqui:

- **Agno importada *dentro* dos métodos**, nunca no topo do módulo. Isso mantém o
  módulo *importável sem a Agno instalada* e confina os tipos Agno estritamente ao
  momento da construção/execução do adapter (Req 12.6).
- **Endpoint por env, nunca hardcoded (Req 15.5)**: o `host` do Ollama vem de
  ``OLLAMA_HOST`` — o `composition_root` lê a env e injeta o valor no construtor.
  Como conveniência, quando `host` não é fornecido, o adapter recorre a
  ``os.environ["OLLAMA_HOST"]`` na hora de montar o `Agent`; o endereço nunca é
  embutido no código.
- **Documentação do produto injetada via `instructions` + `context`** (RF-01), sem
  RAG: o adapter recebe a persona/regras (`instructions`) e um dicionário com a doc
  do produto (`product_context`) e os entrega ao `Agent` da Agno.
- **Detecção de "fora de escopo" (RF-03)**: instruímos o modelo a emitir um
  marcador sentinela (`[OUT_OF_SCOPE]`) quando não conseguir fundamentar a resposta
  na doc. O adapter detecta o marcador, remove-o do texto e marca
  `Answer.out_of_scope=True` — em vez de devolver conteúdo possivelmente alucinado.
- **Tradução de falhas em `ProviderUnavailable` (LSP)**: qualquer erro de
  rede/timeout/configuração vira `ProviderUnavailable(attempts=["ollama"])`, o mesmo
  contrato de erro de todos os adapters, para que o `FallbackLLM` os trate de forma
  uniforme (é só mais uma `LLMPort` na lista).

Testabilidade: o construtor aceita, opcionalmente, um `agent` já pronto ou uma
`agent_factory` — assim os testes injetam um duplo (fake) no lugar do `Agent` da
Agno e verificam a conversão para `Answer` **sem rede** nem SDK. Em produção, nada
é injetado e o adapter monta o `Agent` real da Agno.

Notas sobre a API da Agno v2 (premissas):
- `agno.agent.Agent(model=..., instructions=..., context=...)` monta o agente.
- `agno.models.ollama.Ollama(id=model_id, host=host)` configura o modelo local,
  apontando para o servidor Ollama indicado por ``OLLAMA_HOST``.
- `agent.run(prompt)` retorna um `RunOutput` com o texto em ``.content`` e métricas
  de tokens em ``.metrics`` (campos ``input_tokens``/``output_tokens``); tokens são
  **opcionais** e caem para zero quando ausentes (Req 8.2).
- `agent.run(prompt, stream=True)` retorna um iterável de eventos; o delta de texto
  de cada evento é lido de ``.content`` na ordem de emissão.
Como a superfície exata da Agno v2 pode variar por versão, a extração é defensiva.

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para
Python 3.9+.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer

# Nome do provider que este adapter representa. Usado em `Answer.provider` e na
# lista de `attempts` de `ProviderUnavailable`. Deve casar com a chave em
# `provider_order` (ex.: "openai,ollama").
PROVIDER_NAME = "ollama"

# Nome da variável de ambiente que aponta para o servidor Ollama (Req 15.5). O
# endpoint nunca é embutido no código: vem sempre do ambiente.
OLLAMA_HOST_ENV = "OLLAMA_HOST"

# Marcador sentinela que o modelo deve emitir quando não conseguir fundamentar a
# resposta na documentação fornecida (RF-03). Mantemos o mesmo marcador do
# `OpenAIAdapter` por consistência: detectá-lo é mais robusto e barato do que
# interpretar texto livre; ao encontrá-lo, removemos o marcador e sinalizamos
# out_of_scope.
OUT_OF_SCOPE_MARKER = "[OUT_OF_SCOPE]"

# Instrução acrescentada às `instructions` do usuário para ativar o protocolo do
# marcador. É explícita e determinística: o modelo responde SÓ com o marcador
# quando não puder fundamentar a resposta, evitando alucinação (RF-03).
_OUT_OF_SCOPE_INSTRUCTION = (
    "Responda exclusivamente com base na documentação do produto fornecida no contexto. "
    "Se a resposta não estiver fundamentada nessa documentação, responda APENAS com o "
    f"marcador {OUT_OF_SCOPE_MARKER} e nada mais — não invente informação."
)


class OllamaAdapter:
    """Adapter que envolve um `Agent` da Agno com o modelo Ollama e implementa `LLMPort`.

    Conforma-se ao `LLMPort` de forma **estrutural** (Protocol/duck typing): expõe
    `ask` e `ask_stream` com as assinaturas esperadas, sem herança explícita. É
    substituível por qualquer outro adapter na `provider_order` (LSP): mesmo
    contrato de sucesso (`Answer`) e mesmo contrato de erro (`ProviderUnavailable`).

    Args:
        model_id: Id do modelo Ollama (ex.: ``"llama3.2"``), repassado a
            ``Ollama(id=model_id, host=host)``.
        instructions: Lista de instruções/persona do agente (RF-01). O adapter
            acrescenta a instrução do protocolo de "fora de escopo" ao final.
        product_context: Dicionário com a documentação do produto a ser injetada
            como `context` do `Agent` (sem RAG).
        host: Endpoint do servidor Ollama, proveniente de ``OLLAMA_HOST`` (Req
            15.5). Nunca deve ser hardcoded pelo chamador. Se ``None``, o adapter
            resolve o valor de ``os.environ["OLLAMA_HOST"]`` ao montar o `Agent`.
        agent: (Opcional, para teste) um `Agent` já construído/duplo a ser usado no
            lugar do `Agent` real da Agno. Quando fornecido, a Agno **não** é
            importada e o `host` não é resolvido.
        agent_factory: (Opcional, para teste) fábrica
            ``(model_id, host, instructions, context) -> agent`` usada para
            construir o agente. Precede a construção real via Agno.

    Note:
        A construção do `Agent` é **preguiçosa** (lazy): acontece na primeira
        chamada a `ask`/`ask_stream`, não no `__init__`. Assim, instanciar o
        adapter no `composition_root` não força o import da Agno nem a resolução do
        endpoint antes do primeiro uso.
    """

    def __init__(
        self,
        model_id: str,
        instructions: List[str],
        product_context: Dict[str, Any],
        *,
        host: Optional[str] = None,
        agent: Optional[Any] = None,
        agent_factory: Optional[
            Callable[[str, Optional[str], List[str], Dict[str, Any]], Any]
        ] = None,
    ) -> None:
        self._model_id = model_id
        # Copiamos e acrescentamos a instrução do protocolo de fora de escopo, sem
        # mutar a lista recebida do chamador (evita efeitos colaterais surpreendentes).
        self._instructions: List[str] = list(instructions) + [_OUT_OF_SCOPE_INSTRUCTION]
        self._product_context = dict(product_context)
        self._host = host
        self._agent_factory = agent_factory
        # Cache do agente montado (lazy). Um `agent` injetado é usado diretamente.
        self._agent: Optional[Any] = agent

    # -- Construção do agente (import tardio da Agno) -------------------------

    def _resolve_host(self) -> str:
        """Resolve o endpoint do Ollama, priorizando o valor injetado no construtor.

        Quando `host` não foi fornecido, lê ``OLLAMA_HOST`` do ambiente (Req 15.5) —
        o endereço nunca é embutido no código. A ausência do endpoint é tratada como
        falha de configuração do provider (traduzida em `ProviderUnavailable` pelo
        chamador, LSP).

        Returns:
            O endpoint do servidor Ollama (ex.: ``"http://localhost:11434"``).

        Raises:
            RuntimeError: Se nenhum `host` foi injetado e ``OLLAMA_HOST`` não está
                definido no ambiente.
        """
        if self._host:
            return self._host
        host = os.environ.get(OLLAMA_HOST_ENV)
        if not host:
            raise RuntimeError(
                f"Endpoint do Ollama ausente: defina a variável de ambiente "
                f"'{OLLAMA_HOST_ENV}'."
            )
        return host

    def _build_agent(self) -> Any:
        """Monta (ou obtém do teste) o `Agent` da Agno com o modelo Ollama.

        Importa a Agno **aqui dentro** para manter o import fora do topo do módulo
        (domínio/aplicação nunca importam Agno por transição) e preservar a
        importabilidade do pacote sem o SDK. Quando um `agent_factory` foi injetado,
        delega a ele — caminho usado pelos testes, sem rede.

        Returns:
            Uma instância de ``agno.agent.Agent`` configurada com
            ``Ollama(id=model_id, host=host)``, as `instructions` (persona +
            protocolo de fora de escopo) e o `context` com a doc do produto.
        """
        if self._agent_factory is not None:
            # Caminho de teste: fábrica injetada, sem Agno nem rede.
            return self._agent_factory(
                self._model_id,
                self._host,
                self._instructions,
                self._product_context,
            )

        host = self._resolve_host()

        # Import tardio: a Agno só é carregada quando o adapter é de fato usado.
        from agno.agent import Agent
        from agno.models.ollama import Ollama

        return Agent(
            model=Ollama(id=self._model_id, host=host),
            instructions=self._instructions,
            # Doc do produto injetada como contexto (RF-01, sem RAG).
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
        """Modo *run*: envia o prompt à Agno e converte a resposta em `Answer`.

        Mede a latência ao redor da chamada, extrai os tokens das métricas da Agno
        (com zero quando omitidos e nunca negativos), detecta o marcador de fora de
        escopo e traduz falhas de rede/timeout/configuração em `ProviderUnavailable`.

        Args:
            prompt: A pergunta/prompt já montado.

        Returns:
            Um `Answer` com `text`, `provider="ollama"`, tokens (>= 0), `latency_ms`
            e `out_of_scope` conforme sinalizado pelo modelo.

        Raises:
            ProviderUnavailable: Em qualquer falha do provider (rede, timeout, host
                ausente, etc.), com ``attempts=["ollama"]``.
        """
        start = time.monotonic()
        try:
            agent = self._get_agent()
            run_output = agent.run(prompt)
        except ProviderUnavailable:
            # Já está no contrato correto; não re-embrulhar.
            raise
        except Exception as exc:  # noqa: BLE001 - falha de infra -> contrato comum (LSP)
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

        Chama ``agent.run(prompt, stream=True)`` e repassa o `content` de cada evento
        na ordem recebida. A concatenação dos fragmentos é consistente em conteúdo
        com `ask(prompt).text` para o mesmo prompt/configuração (LSP entre modos,
        Req 2.5). Falhas — na abertura do stream ou durante a iteração — são
        traduzidas em `ProviderUnavailable`.

        Args:
            prompt: A pergunta/prompt já montado.

        Yields:
            Pedaços de texto (`str`) na ordem de emissão. Fragmentos vazios/None são
            ignorados (a Agno pode emitir eventos sem `content`, ex.: eventos de
            controle).

        Raises:
            ProviderUnavailable: Em qualquer falha do provider (rede, timeout, host
                ausente, etc.), com ``attempts=["ollama"]``.
        """
        try:
            agent = self._get_agent()
            stream = agent.run(prompt, stream=True)
            for event in stream:
                chunk = self._extract_chunk_text(event)
                if chunk:
                    yield chunk
        except ProviderUnavailable:
            # Já está no contrato correto; não re-embrulhar.
            raise
        except Exception as exc:  # noqa: BLE001 - falha de infra -> contrato comum (LSP)
            raise ProviderUnavailable(attempts=[PROVIDER_NAME]) from exc

    # ------------------------------------------------------------------ helpers ---

    @staticmethod
    def _extract_text(run_output: Any) -> str:
        """Extrai o texto de resposta do `RunOutput` da Agno.

        A Agno v2 expõe o texto em ``RunOutput.content``. Somos tolerantes: se o
        atributo estiver ausente/None, usamos string vazia.

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
    def _extract_chunk_text(event: Any) -> str:
        """Extrai o delta de texto de um evento de streaming da Agno.

        Aceita tanto eventos com `.content` (Agno v2) quanto strings puras; ignora
        eventos sem texto (retorna ``""``), que o chamador descarta.

        Args:
            event: Um evento emitido por ``agent.run(prompt, stream=True)``.

        Returns:
            O texto do evento, ou ``""`` quando o evento não carrega texto.
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
    def _interpret_scope(text: str) -> "tuple[str, bool]":
        """Detecta o marcador de fora de escopo e o remove do texto.

        Args:
            text: Texto cru retornado pelo modelo.

        Returns:
            Uma tupla ``(texto_limpo, out_of_scope)``. Quando o marcador está
            presente, ``out_of_scope=True`` e o marcador é removido do texto
            (deixando o restante, já sem espaços nas pontas).
        """
        if OUT_OF_SCOPE_MARKER in text:
            cleaned = text.replace(OUT_OF_SCOPE_MARKER, "").strip()
            return cleaned, True
        return text, False

    @staticmethod
    def _extract_tokens(run_output: Any) -> "tuple[int, int]":
        """Extrai ``(prompt_tokens, completion_tokens)`` das métricas da Agno.

        A Agno v2 expõe as métricas em ``RunOutput.metrics`` com os campos
        ``input_tokens``/``output_tokens``. Este helper é defensivo: se `metrics` ou
        os campos estiverem ausentes, retorna zero; valores negativos ou não
        inteiros são normalizados para ``>= 0`` (Req 8.1/8.2: tokens ``>= 0``, zero
        quando omitidos).

        Args:
            run_output: O objeto retornado por ``agent.run(prompt)``.

        Returns:
            Tupla ``(prompt_tokens, completion_tokens)``, ambos inteiros ``>= 0``.
        """
        metrics = getattr(run_output, "metrics", None)
        prompt_tokens = OllamaAdapter._coerce_non_negative_int(
            getattr(metrics, "input_tokens", 0)
        )
        completion_tokens = OllamaAdapter._coerce_non_negative_int(
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
