"""Testes de unidade do `OpenAIAdapter` com um cliente Agno fake (tarefa 7.3).

Foco desta tarefa: exercitar o adapter **sem rede** e sem a Agno real, injetando um duplo
de teste (`agent=`) no lugar do `Agent` da Agno. Verifica-se:

- A conversão da resposta da Agno em `Answer` (provider correto, tokens extraídos com
  ``>= 0`` e zero quando omitidos, `latency_ms >= 0`) — Req 8.1/8.2.
- A detecção do marcador de fora de escopo (`[OUT_OF_SCOPE]`), que marca
  `out_of_scope=True` e remove o marcador do texto — Req 3.1 (RF-03).
- A tradução de qualquer erro de rede/autenticação levantado pelo `run()` em
  `ProviderUnavailable`, preservando o nome do provider em `attempts` — Req 1.2 (LSP).
- A ordem de emissão dos fragmentos em `ask_stream` — Req 8.x/2.5.

O duplo (`FakeAgent`) implementa apenas a superfície da Agno consumida pelo adapter:
``run(prompt)`` (modo run) e ``run(prompt, stream=True)`` (modo stream). Nenhuma chamada de
rede é feita, o que mantém o teste rápido e determinístico (Req 17.2).

**Validates: Requirements 1.2, 3.1, 8.1, 8.2**
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm.openai_adapter import (
    OUT_OF_SCOPE_MARKER,
    PROVIDER_NAME,
    OpenAIAdapter,
)


# --------------------------------------------------------------------------- duplos ---
class FakeMetrics:
    """Métricas no formato objeto (`.input_tokens` / `.output_tokens`), como a Agno v2.

    O adapter lê os tokens via `getattr(metrics, "input_tokens", 0)` etc.; usar um objeto
    (em vez de dict) exercita exatamente esse caminho.
    """

    def __init__(self, input_tokens: Any = None, output_tokens: Any = None) -> None:
        # Só define o atributo quando o valor foi informado, para simular a omissão real
        # de métricas por parte do provider (Req 8.2).
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens


class FakeRunOutput:
    """Resposta completa (RunOutput) da Agno: expõe `.content` e (opcional) `.metrics`."""

    def __init__(self, content: str, metrics: Optional[FakeMetrics] = None) -> None:
        self.content = content
        if metrics is not None:
            self.metrics = metrics


class FakeStreamEvent:
    """Evento de streaming da Agno: expõe `.content` com o delta de texto."""

    def __init__(self, content: Any) -> None:
        self.content = content


class FakeAgent:
    """Duplo do `agno.agent.Agent`, sem rede.

    Implementa apenas o que o `OpenAIAdapter` consome:
    - ``run(prompt)`` -> devolve `run_output` (modo run);
    - ``run(prompt, stream=True)`` -> devolve um iterável de eventos (modo stream).

    Quando `error` é fornecido, `run` levanta esse erro — simulando falha de rede/auth do
    SDK, que o adapter deve traduzir em `ProviderUnavailable`.
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


def _make_adapter(agent: FakeAgent) -> OpenAIAdapter:
    """Monta o adapter injetando o duplo (sem Agno real nem rede)."""
    return OpenAIAdapter(
        model_id="gpt-4o-mini",
        instructions=["Você é um agente de suporte."],
        product_context={"produto": "doc"},
        agent=agent,
    )


# ---------------------------------------------------------------- conversão -> Answer ---
def test_ask_converte_resposta_em_answer_com_provider_e_tokens() -> None:
    """`ask` deve extrair texto, provider e tokens das métricas da resposta."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "Resposta fundamentada.",
            metrics=FakeMetrics(input_tokens=12, output_tokens=34),
        )
    )
    answer = _make_adapter(agent).ask("Como resetar a senha?")

    assert isinstance(answer, Answer)
    assert answer.provider == PROVIDER_NAME == "openai"
    assert answer.text == "Resposta fundamentada."
    assert answer.prompt_tokens == 12
    assert answer.completion_tokens == 34
    assert answer.total_tokens == 46
    assert answer.out_of_scope is False
    assert answer.latency_ms >= 0.0
    # O prompt foi repassado ao agente em modo run (stream=False).
    assert agent.calls == [{"prompt": "Como resetar a senha?", "stream": False}]


def test_ask_tokens_ausentes_caem_para_zero() -> None:
    """Métricas ausentes devem resultar em tokens zero, sem quebrar (Req 8.2)."""
    agent = FakeAgent(run_output=FakeRunOutput("Texto sem métricas"))
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0
    assert answer.total_tokens == 0


def test_ask_tokens_negativos_sao_normalizados_para_zero() -> None:
    """Valores negativos/invalidos de tokens são normalizados para ``>= 0`` (Req 8.1)."""
    agent = FakeAgent(
        run_output=FakeRunOutput(
            "ok", metrics=FakeMetrics(input_tokens=-5, output_tokens="x")
        )
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens == 0
    assert answer.completion_tokens == 0


# ----------------------------------------------------------------- fora de escopo ---
def test_ask_detecta_marcador_fora_de_escopo_e_remove_do_texto() -> None:
    """O marcador sentinela deve setar `out_of_scope=True` e ser removido do texto."""
    agent = FakeAgent(run_output=FakeRunOutput(f"{OUT_OF_SCOPE_MARKER}"))
    answer = _make_adapter(agent).ask("Qual a capital da França?")

    assert answer.out_of_scope is True
    assert OUT_OF_SCOPE_MARKER not in answer.text
    assert answer.text == ""


def test_ask_marcador_no_meio_do_texto_e_removido_mantendo_o_resto() -> None:
    """Ao encontrar o marcador, remove-o e mantém o restante do texto (strip nas pontas)."""
    agent = FakeAgent(
        run_output=FakeRunOutput(f"prefixo {OUT_OF_SCOPE_MARKER} sufixo")
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.out_of_scope is True
    assert OUT_OF_SCOPE_MARKER not in answer.text
    assert answer.text == "prefixo  sufixo"


# --------------------------------------------------- tradução de erro -> ProviderUnavailable ---
def test_ask_traduz_erro_de_rede_em_provider_unavailable() -> None:
    """Erro de rede no `run()` deve virar `ProviderUnavailable(attempts=["openai"])`."""
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
            "ok", metrics=FakeMetrics(input_tokens=input_tokens, output_tokens=output_tokens)
        )
    )
    answer = _make_adapter(agent).ask("pergunta")

    assert answer.prompt_tokens >= 0
    assert answer.completion_tokens >= 0
    assert answer.prompt_tokens == input_tokens
    assert answer.completion_tokens == output_tokens
    assert answer.latency_ms >= 0.0
