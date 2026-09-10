"""Correlação do `request_id` entre log e span, e ausência de PII (Req 16.4/16.5).

Este módulo cobre o **wiring** da tarefa 17.2: o `FallbackLLM` gera **um**
`request_id` por requisição, anexa-o ao span OTel atual (atributo `app.request_id`
via `set_request_id_on_span`) e propaga o **mesmo** valor a todos os
`log_request`. Aqui asserimos que:

- o valor anexado ao span é **idêntico** ao gravado no(s) registro(s) de log
  (correlação log ↔ trace, Req 16.4);
- o `request_id` é um identificador **opaco** e não vazio (sem PII);
- a pergunta em claro e uma chave de API **nunca** aparecem no que é logado; a
  pergunta consta apenas como `question_hash` (Req 16.5).

Estratégia (sem rede, sem OTel real): substituímos `set_request_id_on_span` no
namespace do módulo `fallback_llm` por um duplo que apenas **captura** o valor que
seria anexado ao span. Um logger em memória grava as chamadas a `log_request`.
Assim o teste é determinístico e não depende do SDK do OpenTelemetry. A validação
com um exporter OTel **em memória** (Property 13) é responsabilidade da tarefa 17.4.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm import fallback_llm as fallback_llm_module
from suporte.infrastructure.llm.fallback_llm import FallbackLLM
from suporte.infrastructure.observability.logging import hash_question


# Pergunta e chave "sensíveis" que NUNCA devem aparecer no que é logado.
PLAINTEXT_QUESTION = "Como eu troco a bateria do produto X?"
FAKE_API_KEY = "sk-test-SUPERSECRETKEY1234567890"


# --------------------------------------------------------------------- fakes ---
class AlwaysOk:
    """`LLMPort` fake que responde sempre com sucesso e no ato (dentro do SLA)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text="resposta",
            provider=self.name,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield "resposta"


class AlwaysFails:
    """`LLMPort` fake que sempre falha com `ProviderUnavailable`."""

    def __init__(self, name: str) -> None:
        self.name = name

    def ask(self, prompt: str) -> Answer:
        raise ProviderUnavailable(attempts=[self.name])

    def ask_stream(self, prompt: str) -> Iterator[str]:
        raise ProviderUnavailable(attempts=[self.name])


class RecordingLogger:
    """Logger em memória que grava cada chamada a `log_request` (inclui request_id)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def log_request(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


@pytest.fixture()
def captured_span_ids(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Captura os valores que seriam anexados ao span via `set_request_id_on_span`.

    Substitui a função no namespace do módulo `fallback_llm` (onde ela é chamada),
    de modo que o teste não dependa do SDK do OpenTelemetry.
    """
    captured: List[str] = []
    monkeypatch.setattr(
        fallback_llm_module,
        "set_request_id_on_span",
        lambda request_id: captured.append(request_id),
    )
    return captured


def _build(providers: List[Any], logger: RecordingLogger) -> FallbackLLM:
    """Monta um `FallbackLLM` rápido e determinístico (sem espera real)."""
    return FallbackLLM(
        providers,
        per_attempt_timeout_s=1.0,
        total_timeout_s=5.0,
        max_attempts_per_provider=1,
        backoff_base_s=0.0,
        logger=logger,
        clock=lambda: 0.0,
        sleep=lambda _s: None,
    )


class TestCorrelacaoRequestId:
    """O mesmo `request_id` aparece no span e em todos os registros de log (Req 16.4)."""

    def test_ask_ok_mesmo_request_id_no_span_e_no_log(
        self, captured_span_ids: List[str]
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        fallback.ask(PLAINTEXT_QUESTION)

        # Exatamente um request_id foi anexado ao span.
        assert len(captured_span_ids) == 1
        span_request_id = captured_span_ids[0]
        assert isinstance(span_request_id, str) and span_request_id != ""

        # Todo registro de log carrega o MESMO request_id.
        assert logger.calls, "esperava ao menos um registro de log"
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    def test_ask_erro_total_mesmo_request_id_no_span_e_no_log(
        self, captured_span_ids: List[str]
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysFails("openai"), AlwaysFails("groq")], logger)

        with pytest.raises(ProviderUnavailable):
            fallback.ask(PLAINTEXT_QUESTION)

        assert len(captured_span_ids) == 1
        span_request_id = captured_span_ids[0]
        assert logger.calls
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    def test_ask_stream_mesmo_request_id_no_span_e_no_log(
        self, captured_span_ids: List[str]
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        list(fallback.ask_stream(PLAINTEXT_QUESTION))

        assert len(captured_span_ids) == 1
        span_request_id = captured_span_ids[0]
        assert logger.calls
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    def test_request_ids_distintos_entre_requisicoes(
        self, captured_span_ids: List[str]
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        fallback.ask(PLAINTEXT_QUESTION)
        fallback.ask(PLAINTEXT_QUESTION)

        assert len(captured_span_ids) == 2
        # Ids únicos por requisição.
        assert captured_span_ids[0] != captured_span_ids[1]


class TestAusenciaDePII:
    """A pergunta em claro e a chave nunca vazam; só `question_hash` (Req 16.5)."""

    def test_log_registra_pergunta_apenas_como_hash(self) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        fallback.ask(PLAINTEXT_QUESTION)

        expected_hash = hash_question(PLAINTEXT_QUESTION)
        for call in logger.calls:
            assert call["question_hash"] == expected_hash
            # A pergunta em claro e a chave não aparecem em nenhum campo logado.
            serialized = repr(call)
            assert PLAINTEXT_QUESTION not in serialized
            assert "bateria" not in serialized
            assert FAKE_API_KEY not in serialized
