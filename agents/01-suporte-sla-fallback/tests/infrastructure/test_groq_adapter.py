"""Testes de unidade do `GroqAdapter` com um cliente Agno fake (tarefa 7.3).

Foco desta tarefa: exercitar o adapter **sem rede** e sem a Agno real, injetando um duplo
via `agent_factory=` (callable que devolve o agente fake). Verifica-se:

- A conversão da resposta da Agno em `Answer` (provider ``"groq"``, tokens ``>= 0`` e zero
  quando omitidos, `latency_ms >= 0`) — Req 8.1/8.2. As métricas são exercitadas nas duas
  formas suportadas: `dict` (``{"input_tokens": .., "output_tokens": ..}``) e objeto.
- A detecção do marcador de fora de escopo (`[[OUT_OF_SCOPE]]`), que marca
  `out_of_scope=True` e suprime o texto não fundamentado — Req 3.1 (RF-03).
- A tradução de qualquer erro de rede/autenticação levantado pelo `run()` em
  `ProviderUnavailable`, preservando o nome do provider em `attempts` — Req 1.2 (LSP).
- A ordem de emissão dos fragmentos em `ask_stream` — Req 8.x/2.5.

O `GroqAdapter` monta o agente de forma preguiçosa; a `agent_factory` só é chamada na
primeira chamada a `ask`/`ask_stream`. Nenhuma chamada de rede é feita (Req 17.2).

**Validates: Requirements 1.2, 3.1, 8.1, 8.2**
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm.groq_adapter import (
    OUT_OF_SCOPE_MARKER,
    PROVIDER_NAME,
    GroqAdapter,
)


# --------------------------------------------------------------------------- duplos ---
class FakeMetricsObject:
    """Métricas na forma de objeto (`.input_tokens` / `.output_tokens`)."""

    def __init__(self, input_tokens: Any = None, output_tokens: Any = None) -> None:
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens


class FakeRunOutput:
    """Resposta completa da Agno: `.content` e (opcional) `.metrics` (dict ou objeto)."""

    def __init__(self, content: str, metrics: Any = None) -> None:
        self.content = content
        if metrics is not None:
            self.metrics = metrics


class FakeStreamEvent:
    """Evento de streaming da Agno: expõe `.content` com o delta de texto."""

    def __init__(self, content: Any) -> None:
        self.content = content


class FakeAgent:
    """Duplo do `agno.agent.Agent` com o modelo Groq, sem rede.

    Implementa a superfície consumida pelo `GroqAdapter`:
    - ``run(prompt)`` -> `run_output` (modo run);
    - ``run(prompt, stream=True)`` -> iterável de eventos (modo stream).

    Quando `error` é fornecido, `run` levanta esse erro (simula falha de rede/auth).
    """

    def __init__(
        self,
        run_output: Any = None,
        stream_events: Optional[Iterable[Any]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self._run_output = run_output
        self._stream_events = list(stream_events) if stream_events is not None else []
        self._error = error
        self.calls: List[dict] = []

    def run(self, prompt: str, stream: bool = False) -> Any:
        self.calls.append({"prompt": prompt, "stream": stream})
        if self._error is not None:
            raise self._error
        if stream:
            return iter(self._stream_events)
        return self._run_output


def _make_adapter(agent: FakeAgent) -> GroqAdapter:
    """Monta o adapter injetando o duplo via `agent_factory` (sem Agno real nem rede)."""
    return GroqAdapter(
        model_id="llama-3.3-70b-versatile",
        instructions=["Você é um agente de suporte."],
        product_context={"produto": "doc"},
        agent_factory=lambda: agent,
    )


# ---------------------------------------------------------------- conversão -> Answer ---
def test_ask_converte_resposta_em_answer_com_metrics_dict() -> None:
    """`ask` extrai texto, provider e tokens de métricas em formato `dict`."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "Resposta fundamentada.",
            metrics={"input_tokens": 20, "output_tokens": 7},
        )
    )
    answer = _make_adapter(agent).ask("Como abrir um chamado?")

    assert isinstance(answer, Answer)
    assert answer.provider == PROVIDER_NAME == "groq"
    assert answer.text == "Resposta fundamentada."
    assert answer.prompt_tokens == 20
    assert answer.completion_tokens == 7
    assert answer.total_tokens == 27
    assert answer.out_of_scope is False
    assert answer.latency_ms >= 0.0
    assert agent.calls == [{"prompt": "Como abrir um chamado?", "stream": False}]


def test_ask_converte_resposta_em_answer_com_metrics_objeto() -> None:
    """`ask` também extrai tokens de métricas na forma de objeto (aliases via getattr)."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "ok", metrics=FakeMetricsObject(input_tokens=3, output_tokens=9)
        )
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens == 3
    assert answer.completion_tokens == 9


def test_ask_tokens_ausentes_caem_para_zero() -> None:
    """Sem `metrics`, os tokens caem para zero, sem quebrar (Req 8.2)."""
    agent = FakeAgent(run_output=FakeRunOutput("Texto sem métricas"))
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0
    assert answer.total_tokens == 0


def test_ask_tokens_negativos_sao_normalizados_para_zero() -> None:
    """Valores negativos/invalidos de tokens são normalizados para ``>= 0`` (Req 8.1)."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "ok", metrics={"input_tokens": -3, "output_tokens": "x"}
        )
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0


# ----------------------------------------------------------------- fora de escopo ---
def test_ask_detecta_marcador_fora_de_escopo_e_suprime_texto() -> None:
    """O marcador sentinela deve setar `out_of_scope=True` e suprimir o texto."""
    agent = FakeAgent(run_output=FakeRunOutput(f"algo {OUT_OF_SCOPE_MARKER}"))
    answer = _make_adapter(agent).ask("Qual a capital da França?")

    assert answer.out_of_scope is True
    assert OUT_OF_SCOPE_MARKER not in answer.text
    assert answer.text == ""


# --------------------------------------------------- tradução de erro -> ProviderUnavailable ---
def test_ask_traduz_erro_de_rede_em_provider_unavailable() -> None:
    """Erro de rede no `run()` deve virar `ProviderUnavailable(attempts=["groq"])`."""
    agent = FakeAgent(error=ConnectionError("timeout de rede"))
    with pytest.raises(ProviderUnavailable) as exc_info:
        _make_adapter(agent).ask("pergunta")
    assert PROVIDER_NAME in exc_info.value.attempts


def test_ask_traduz_erro_de_auth_em_provider_unavailable() -> None:
    """Erro de autenticação no `run()` também deve virar `ProviderUnavailable`."""
    agent = FakeAgent(error=PermissionError("401 Unauthorized"))
    with pytest.raises(ProviderUnavailable) as exc_info:
        _make_adapter(agent).ask("pergunta")
    assert PROVIDER_NAME in exc_info.value.attempts


# --------------------------------------------------------------------- ask_stream ---
def test_ask_stream_preserva_a_ordem_dos_fragmentos() -> None:
    """Os fragmentos devem ser emitidos na mesma ordem dos eventos da Agno."""
    events = [FakeStreamEvent("Olá"), FakeStreamEvent(", "), FakeStreamEvent("mundo!")]
    agent = FakeAgent(stream_events=events)
    chunks = list(_make_adapter(agent).ask_stream("pergunta"))

    assert chunks == ["Olá", ", ", "mundo!"]
    assert "".join(chunks) == "Olá, mundo!"
    assert agent.calls == [{"prompt": "pergunta", "stream": True}]


def test_ask_stream_ignora_eventos_sem_conteudo() -> None:
    """Eventos sem `content` (controle) são descartados, preservando a ordem do resto."""
    events = [
        FakeStreamEvent("a"),
        FakeStreamEvent(None),
        FakeStreamEvent(""),
        FakeStreamEvent("b"),
    ]
    agent = FakeAgent(stream_events=events)
    chunks = list(_make_adapter(agent).ask_stream("pergunta"))

    assert chunks == ["a", "b"]


def test_ask_stream_traduz_erro_de_abertura_em_provider_unavailable() -> None:
    """Falha ao abrir o stream deve virar `ProviderUnavailable`."""
    agent = FakeAgent(error=ConnectionError("falha ao abrir stream"))
    with pytest.raises(ProviderUnavailable) as exc_info:
        list(_make_adapter(agent).ask_stream("pergunta"))
    assert PROVIDER_NAME in exc_info.value.attempts


# --------------------------------------------------------------- property-based ---
@given(
    input_tokens=st.integers(min_value=0, max_value=10_000),
    output_tokens=st.integers(min_value=0, max_value=10_000),
)
def test_ask_tokens_nunca_negativos_property(
    input_tokens: int, output_tokens: int
) -> None:
    """Para quaisquer contagens válidas, os tokens do `Answer` são ``>= 0`` e coerentes."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "ok", metrics={"input_tokens": input_tokens, "output_tokens": output_tokens}
        )
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens >= 0
    assert answer.completion_tokens >= 0
    assert answer.prompt_tokens == input_tokens
    assert answer.completion_tokens == output_tokens
    assert answer.latency_ms >= 0.0
