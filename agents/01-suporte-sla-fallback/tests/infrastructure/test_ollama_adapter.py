"""Testes de unidade do `OllamaAdapter` (infrastructure/llm/ollama_adapter.py).

Foco desta tarefa (16.2): validar o adapter do provider Ollama **sem rede** nem SDK,
injetando um duplo (fake) no lugar do `Agent`/modelo Ollama da Agno. Verificamos:

- Conversão da resposta da Agno em `Answer`: `provider="ollama"`, tokens (>= 0, zero
  quando omitidos) e `latency_ms >= 0` (Req 8.1/8.2, 15.1).
- Detecção do marcador de fora de escopo (`[OUT_OF_SCOPE]`): remoção do marcador do
  texto e sinalização de `out_of_scope=True` (RF-03).
- Tradução de falha de rede/timeout do `run()` em `ProviderUnavailable(attempts=["ollama"])`
  — o mesmo contrato de erro dos demais adapters (LSP).
- Ordem de emissão dos fragmentos no modo *stream*.
- Ausência do endpoint (`OLLAMA_HOST` não definido e nenhum `host`/agent injetado)
  traduzida em `ProviderUnavailable` (Req 15.5).

Como a Agno é importada *dentro* dos métodos e o construtor aceita `agent`/`agent_factory`,
todos os testes usam duplos em memória: nenhum toca a rede nem carrega a Agno.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm.ollama_adapter import (
    OLLAMA_HOST_ENV,
    OUT_OF_SCOPE_MARKER,
    PROVIDER_NAME,
    OllamaAdapter,
)


# --------------------------------------------------------------------------- fakes ---


class _FakeRunOutput:
    """Duplo do `RunOutput` da Agno v2: texto em `.content`, tokens em `.metrics`."""

    def __init__(
        self,
        content: str,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        *,
        with_metrics: bool = True,
    ) -> None:
        self.content = content
        if with_metrics:
            self.metrics = SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        else:
            self.metrics = None


class _FakeAgent:
    """Duplo do `Agent` da Agno: `run(prompt)` e `run(prompt, stream=True)`.

    Não faz rede. Registra o último prompt recebido para inspeção e devolve uma
    resposta pré-configurada (modo run) ou uma sequência de eventos (modo stream).
    """

    def __init__(
        self,
        run_output: Optional[_FakeRunOutput] = None,
        stream_events: Optional[List[Any]] = None,
    ) -> None:
        self._run_output = run_output
        self._stream_events = stream_events or []
        self.last_prompt: Optional[str] = None

    def run(self, prompt: str, stream: bool = False) -> Any:
        self.last_prompt = prompt
        if stream:
            return iter(self._stream_events)
        return self._run_output


class _RaisingAgent:
    """Duplo que simula falha de rede/timeout do provider ao chamar `run`."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def run(self, prompt: str, stream: bool = False) -> Any:
        raise self._exc


def _make_adapter(agent: Any) -> OllamaAdapter:
    """Constrói o adapter com um `agent` fake já pronto (sem Agno, sem rede)."""
    return OllamaAdapter(
        model_id="llama3.2",
        instructions=["Você é um atendente de suporte."],
        product_context={"doc": "Documentação do produto."},
        agent=agent,
    )


# ------------------------------------------------------------------ conversão Answer ---


def test_ask_converts_run_output_to_answer() -> None:
    """`ask` converte o `RunOutput` da Agno em `Answer` com provider e tokens corretos."""
    agent = _FakeAgent(
        run_output=_FakeRunOutput("Olá, como posso ajudar?", input_tokens=12, output_tokens=8)
    )
    adapter = _make_adapter(agent)

    answer = adapter.ask("Qual o horário de atendimento?")

    assert isinstance(answer, Answer)
    assert answer.text == "Olá, como posso ajudar?"
    assert answer.provider == PROVIDER_NAME == "ollama"
    assert answer.prompt_tokens == 12
    assert answer.completion_tokens == 8
    assert answer.total_tokens == 20
    assert answer.out_of_scope is False
    assert answer.latency_ms >= 0.0
    # O prompt chega ao agente sem alteração.
    assert agent.last_prompt == "Qual o horário de atendimento?"


def test_ask_tokens_default_to_zero_when_omitted() -> None:
    """Tokens ausentes/None nas métricas caem para zero (Req 8.2)."""
    agent = _FakeAgent(run_output=_FakeRunOutput("resposta", input_tokens=None, output_tokens=None))
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0
    assert answer.total_tokens == 0


def test_ask_tokens_default_to_zero_when_metrics_missing() -> None:
    """Sem objeto `metrics`, os tokens são zero (extração defensiva)."""
    agent = _FakeAgent(run_output=_FakeRunOutput("resposta", with_metrics=False))
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0


def test_ask_negative_tokens_are_clamped_to_zero() -> None:
    """Valores negativos de token são normalizados para >= 0 (Req 8.1)."""
    agent = _FakeAgent(run_output=_FakeRunOutput("resposta", input_tokens=-5, output_tokens=-1))
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0


def test_ask_missing_content_yields_empty_text() -> None:
    """Conteúdo None vira string vazia, sem quebrar a conversão."""
    agent = _FakeAgent(run_output=_FakeRunOutput(content=None, input_tokens=1, output_tokens=1))
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.text == ""


# ---------------------------------------------------------------- fora de escopo (RF-03) ---


def test_ask_detects_and_strips_out_of_scope_marker() -> None:
    """Marcador de fora de escopo é detectado, removido e sinaliza `out_of_scope=True`."""
    agent = _FakeAgent(
        run_output=_FakeRunOutput(f"{OUT_OF_SCOPE_MARKER}", input_tokens=3, output_tokens=0)
    )
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta fora do escopo")

    assert answer.out_of_scope is True
    assert OUT_OF_SCOPE_MARKER not in answer.text
    assert answer.text == ""


def test_ask_strips_marker_but_keeps_remaining_text() -> None:
    """Quando há texto junto ao marcador, o restante é preservado (sem o marcador)."""
    agent = _FakeAgent(
        run_output=_FakeRunOutput(f"  texto extra {OUT_OF_SCOPE_MARKER}  ")
    )
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.out_of_scope is True
    assert OUT_OF_SCOPE_MARKER not in answer.text
    assert answer.text == "texto extra"


def test_ask_in_scope_answer_is_not_flagged() -> None:
    """Resposta comum (sem marcador) mantém `out_of_scope=False`."""
    agent = _FakeAgent(run_output=_FakeRunOutput("Resposta fundamentada na doc."))
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.out_of_scope is False
    assert answer.text == "Resposta fundamentada na doc."


# ------------------------------------------------- tradução de falhas -> ProviderUnavailable ---


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("conexão recusada pelo servidor Ollama"),
        TimeoutError("estouro de timeout"),
        RuntimeError("erro inesperado do SDK"),
    ],
)
def test_ask_network_error_becomes_provider_unavailable(exc: Exception) -> None:
    """Erro de rede/timeout do `run()` vira `ProviderUnavailable(attempts=["ollama"])` (LSP)."""
    adapter = _make_adapter(_RaisingAgent(exc))

    with pytest.raises(ProviderUnavailable) as excinfo:
        adapter.ask("pergunta")

    assert excinfo.value.attempts == [PROVIDER_NAME]
    # A causa original é preservada para diagnóstico, sem vazar ao usuário.
    assert excinfo.value.__cause__ is exc


def test_ask_missing_host_becomes_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem `host`, sem `agent` e sem `OLLAMA_HOST` no ambiente -> `ProviderUnavailable`."""
    monkeypatch.delenv(OLLAMA_HOST_ENV, raising=False)
    adapter = OllamaAdapter(
        model_id="llama3.2",
        instructions=["persona"],
        product_context={"doc": "x"},
    )

    with pytest.raises(ProviderUnavailable) as excinfo:
        adapter.ask("pergunta")

    assert excinfo.value.attempts == [PROVIDER_NAME]


# ------------------------------------------------------------------------ modo stream ---


def test_ask_stream_yields_fragments_in_order() -> None:
    """`ask_stream` repassa o `.content` de cada evento na ordem de emissão."""
    events = [
        SimpleNamespace(content="Olá"),
        SimpleNamespace(content=", "),
        SimpleNamespace(content="mundo"),
        SimpleNamespace(content="!"),
    ]
    adapter = _make_adapter(_FakeAgent(stream_events=events))

    chunks = list(adapter.ask_stream("pergunta"))

    assert chunks == ["Olá", ", ", "mundo", "!"]
    assert "".join(chunks) == "Olá, mundo!"


def test_ask_stream_skips_empty_and_none_events() -> None:
    """Eventos sem texto (None/vazio) são ignorados; strings puras são aceitas."""
    events = [
        SimpleNamespace(content="a"),
        SimpleNamespace(content=None),  # evento de controle, sem texto
        SimpleNamespace(content=""),
        "b",  # string pura também é aceita
    ]
    adapter = _make_adapter(_FakeAgent(stream_events=events))

    chunks = list(adapter.ask_stream("pergunta"))

    assert chunks == ["a", "b"]


def test_ask_stream_network_error_becomes_provider_unavailable() -> None:
    """Falha ao abrir/iterar o stream vira `ProviderUnavailable(attempts=["ollama"])`."""
    adapter = _make_adapter(_RaisingAgent(ConnectionError("stream indisponível")))

    with pytest.raises(ProviderUnavailable) as excinfo:
        list(adapter.ask_stream("pergunta"))

    assert excinfo.value.attempts == [PROVIDER_NAME]


def test_ask_stream_missing_host_becomes_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ausência de endpoint também é traduzida em `ProviderUnavailable` no modo stream."""
    monkeypatch.delenv(OLLAMA_HOST_ENV, raising=False)
    adapter = OllamaAdapter(
        model_id="llama3.2",
        instructions=["persona"],
        product_context={"doc": "x"},
    )

    with pytest.raises(ProviderUnavailable) as excinfo:
        list(adapter.ask_stream("pergunta"))

    assert excinfo.value.attempts == [PROVIDER_NAME]


# --------------------------------------------------------- agent_factory (injeção sem rede) ---


def test_agent_factory_is_used_and_receives_config() -> None:
    """Quando `agent_factory` é injetado, ele recebe (model_id, host, instructions, context)."""
    captured: dict = {}

    def factory(model_id, host, instructions, context):
        captured["model_id"] = model_id
        captured["host"] = host
        captured["instructions"] = instructions
        captured["context"] = context
        return _FakeAgent(run_output=_FakeRunOutput("via factory", input_tokens=1, output_tokens=2))

    adapter = OllamaAdapter(
        model_id="llama3.2",
        instructions=["persona"],
        product_context={"doc": "conteúdo"},
        host="http://localhost:11434",
        agent_factory=factory,
    )

    answer = adapter.ask("pergunta")

    assert answer.text == "via factory"
    assert answer.provider == "ollama"
    assert captured["model_id"] == "llama3.2"
    assert captured["host"] == "http://localhost:11434"
    # A instrução do protocolo de fora de escopo é acrescentada à persona do usuário.
    assert captured["instructions"][0] == "persona"
    assert len(captured["instructions"]) == 2
    assert captured["context"] == {"doc": "conteúdo"}


# ----------------------------------------------------------- property-based (tokens/latência) ---


@given(
    text=st.text(max_size=200),
    input_tokens=st.integers(min_value=-100, max_value=10_000),
    output_tokens=st.integers(min_value=-100, max_value=10_000),
)
def test_answer_invariants_hold_for_arbitrary_outputs(
    text: str, input_tokens: int, output_tokens: int
) -> None:
    """Propriedade: para qualquer saída, `Answer` é sempre válido.

    provider == "ollama", tokens >= 0, latency_ms >= 0 e total_tokens consistente.
    Excluímos textos que contenham o marcador para isolar a invariante de conversão
    do comportamento de fora de escopo (coberto por testes dedicados).

    **Validates: Requirements 15.1**
    """
    # Evita colisão acidental com o protocolo de fora de escopo.
    assume_no_marker = OUT_OF_SCOPE_MARKER not in text
    if not assume_no_marker:
        return

    agent = _FakeAgent(
        run_output=_FakeRunOutput(text, input_tokens=input_tokens, output_tokens=output_tokens)
    )
    adapter = _make_adapter(agent)

    answer = adapter.ask("pergunta")

    assert answer.provider == "ollama"
    assert answer.prompt_tokens >= 0
    assert answer.completion_tokens >= 0
    assert answer.latency_ms >= 0.0
    assert answer.total_tokens == answer.prompt_tokens + answer.completion_tokens
    assert answer.out_of_scope is False
