"""Property 13 — Correlação `request_id` e ausência de PII (Req 16.3/16.4/16.5).

Este módulo cobre a **Property 13** do design com o mecanismo exigido pela tarefa
17.4: um **exporter OpenTelemetry EM MEMÓRIA** (`InMemorySpanExporter`) ligado a um
`TracerProvider` local via `SimpleSpanProcessor`, mais um **log em memória**. Ao
contrário da tarefa 17.2 (que captura o valor via um duplo de
`set_request_id_on_span`), aqui exercitamos o caminho **real** do OpenTelemetry:

- montamos um `TracerProvider` de teste com um `InMemorySpanExporter`;
- abrimos um span real e, **dentro** desse contexto, executamos uma requisição do
  `FallbackLLM` (que chama `set_request_id_on_span`, o qual usa
  `trace.get_current_span().set_attribute("app.request_id", ...)`);
- ao encerrar o span, lemos os spans finalizados no exporter em memória e
  asserimos que o atributo `app.request_id` do span é **idêntico** ao campo
  `request_id` gravado em **todos** os registros do log em memória (Req 16.4);
- asserimos que nenhuma PII, o **texto da pergunta em claro** ou uma **chave de
  API** aparecem no log ou nos atributos do span — a pergunta consta apenas como
  `question_hash` (Req 16.5).

Sem rede: o exporter é em memória; nenhum endpoint OTLP é contatado. Usamos
`hypothesis` para variar a pergunta (texto arbitrário), reforçando a
quantificação universal da propriedade sobre o espaço de perguntas.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Tuple

import pytest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# --- OpenTelemetry SDK: exporter em memória (Req 16.4, P13) --------------------
# Se o SDK do OpenTelemetry não estiver instalado, a Property 13 não pode ser
# exercitada pelo caminho real — pulamos com uma mensagem clara em vez de falhar.
otel = pytest.importorskip(
    "opentelemetry.sdk.trace",
    reason="OpenTelemetry SDK necessario para exercitar a Property 13 (exporter em memoria)",
)

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from suporte.domain.exceptions import ProviderUnavailable  # noqa: E402
from suporte.domain.ports import Answer  # noqa: E402
from suporte.infrastructure.llm.fallback_llm import FallbackLLM  # noqa: E402
from suporte.infrastructure.observability.logging import hash_question  # noqa: E402


# Chave de atributo que correlaciona log e trace (o mesmo valor do log).
SPAN_REQUEST_ID_ATTRIBUTE = "app.request_id"

# Pergunta e chave "sensíveis" que NUNCA devem aparecer no log ou no span.
PLAINTEXT_QUESTION = "Como eu troco a bateria do produto X?"
SENSITIVE_SUBSTRING = "bateria"
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
    """Logger em memória: grava cada chamada a `log_request` (inclui request_id)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def log_request(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


# ------------------------------------------------------- OTel em memória (fix) --
class _InMemoryTracing:
    """Encapsula um `TracerProvider` de teste com `InMemorySpanExporter`.

    Fornece um `tracer` para abrir spans reais e um exporter em memória de onde
    ler os spans finalizados (com seus atributos) após o encerramento do span.
    Não toca o `TracerProvider` global do processo: o `set_request_id_on_span`
    do módulo usa `trace.get_current_span()`, que resolve o span **ativo no
    contexto** — por isso executamos a requisição dentro de
    `tracer.start_as_current_span(...)`.
    """

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("test.property13")

    def finished_spans(self) -> Tuple[Any, ...]:
        return self.exporter.get_finished_spans()


@pytest.fixture()
def tracing() -> _InMemoryTracing:
    """Um pipeline OTel em memória, isolado por teste."""
    return _InMemoryTracing()


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


def _span_request_id(span: Any) -> Any:
    """Extrai o atributo `app.request_id` de um span exportado (ou None)."""
    attributes = getattr(span, "attributes", {}) or {}
    return attributes.get(SPAN_REQUEST_ID_ATTRIBUTE)


# ------------------------------------------------------------- Property 13 -----
class TestProperty13CorrelacaoRequestId:
    """O mesmo `request_id` no atributo `app.request_id` do span e no log (Req 16.4).

    **Validates: Requirements 16.4**
    """

    def test_ask_ok_mesmo_request_id_no_span_exportado_e_no_log(
        self, tracing: _InMemoryTracing
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        # Executa a requisição DENTRO de um span real: `set_request_id_on_span`
        # anexa `app.request_id` ao span ativo do contexto.
        with tracing.tracer.start_as_current_span("answer_question"):
            fallback.ask(PLAINTEXT_QUESTION)

        # Ao fechar o span, ele é exportado para a memória: exatamente um span.
        spans = tracing.finished_spans()
        assert len(spans) == 1
        span_request_id = _span_request_id(spans[0])
        assert isinstance(span_request_id, str) and span_request_id != ""

        # Todo registro de log carrega o MESMO request_id do atributo do span.
        assert logger.calls, "esperava ao menos um registro de log"
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    def test_ask_erro_total_mesmo_request_id_no_span_exportado_e_no_log(
        self, tracing: _InMemoryTracing
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysFails("openai"), AlwaysFails("groq")], logger)

        with tracing.tracer.start_as_current_span("answer_question"):
            with pytest.raises(ProviderUnavailable):
                fallback.ask(PLAINTEXT_QUESTION)

        spans = tracing.finished_spans()
        assert len(spans) == 1
        span_request_id = _span_request_id(spans[0])
        assert isinstance(span_request_id, str) and span_request_id != ""

        assert logger.calls
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    def test_ask_stream_mesmo_request_id_no_span_exportado_e_no_log(
        self, tracing: _InMemoryTracing
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        with tracing.tracer.start_as_current_span("answer_question"):
            list(fallback.ask_stream(PLAINTEXT_QUESTION))

        spans = tracing.finished_spans()
        assert len(spans) == 1
        span_request_id = _span_request_id(spans[0])
        assert isinstance(span_request_id, str) and span_request_id != ""

        assert logger.calls
        for call in logger.calls:
            assert call["request_id"] == span_request_id

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(question=st.text(max_size=300))
    def test_property_mesmo_request_id_para_qualquer_pergunta(
        self, tracing: _InMemoryTracing, question: str
    ) -> None:
        # ∀ pergunta: o request_id do span exportado == request_id de cada log,
        # é opaco/não vazio, e a pergunta entra só como question_hash (Req 16.4/16.5).
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        # A fixture é reutilizada entre exemplos do hypothesis; limpamos os spans
        # acumulados para asserir apenas sobre o span desta requisição.
        tracing.exporter.clear()

        with tracing.tracer.start_as_current_span("answer_question"):
            fallback.ask(question)

        spans = tracing.finished_spans()
        assert len(spans) == 1
        span_request_id = _span_request_id(spans[0])
        assert isinstance(span_request_id, str) and span_request_id != ""

        expected_hash = hash_question(question)
        assert logger.calls
        for call in logger.calls:
            assert call["request_id"] == span_request_id
            assert call["question_hash"] == expected_hash


class TestProperty13AusenciaDePII:
    """Nem a pergunta em claro nem a chave vazam no log ou no span; só hash (Req 16.5).

    **Validates: Requirements 16.5**
    """

    def test_log_registra_pergunta_apenas_como_hash(
        self, tracing: _InMemoryTracing
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        with tracing.tracer.start_as_current_span("answer_question"):
            fallback.ask(PLAINTEXT_QUESTION)

        expected_hash = hash_question(PLAINTEXT_QUESTION)
        for call in logger.calls:
            assert call["question_hash"] == expected_hash
            serialized = repr(call)
            assert PLAINTEXT_QUESTION not in serialized
            assert SENSITIVE_SUBSTRING not in serialized
            assert FAKE_API_KEY not in serialized

    def test_span_exportado_nao_contem_pergunta_em_claro_nem_chave(
        self, tracing: _InMemoryTracing
    ) -> None:
        logger = RecordingLogger()
        fallback = _build([AlwaysOk("openai")], logger)

        # Simula um cenário em que a chave também estaria "por perto" do fluxo:
        # o prompt é a pergunta em claro; nada disso deve chegar ao span.
        with tracing.tracer.start_as_current_span("answer_question"):
            fallback.ask(PLAINTEXT_QUESTION)

        spans = tracing.finished_spans()
        assert len(spans) == 1
        span = spans[0]

        # O único atributo relacionado à correlação é o request_id opaco.
        span_request_id = _span_request_id(span)
        assert isinstance(span_request_id, str) and span_request_id != ""
        # O request_id não é a pergunta nem a chave.
        assert PLAINTEXT_QUESTION not in span_request_id
        assert FAKE_API_KEY not in span_request_id

        # Nenhum atributo do span (chave ou valor) contém a pergunta em claro,
        # o trecho sensível ou a chave de API.
        attributes = getattr(span, "attributes", {}) or {}
        serialized_attrs = repr(dict(attributes))
        assert PLAINTEXT_QUESTION not in serialized_attrs
        assert SENSITIVE_SUBSTRING not in serialized_attrs
        assert FAKE_API_KEY not in serialized_attrs
