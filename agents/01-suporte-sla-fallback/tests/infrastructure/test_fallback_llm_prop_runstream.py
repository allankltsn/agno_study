"""Teste de propriedade do `FallbackLLM` — Property 7: Consistência run/stream (tarefa 8.5).

Foco desta tarefa: garantir que, para um provider **consistente** (aquele cujo `ask`
retorna exatamente a concatenação dos fragmentos emitidos por `ask_stream`), o
`FallbackLLM` preserva essa consistência entre os dois modos. Ou seja, para qualquer
lista de fragmentos e qualquer prompt:

    "".join(fallback.ask_stream(prompt)) == fallback.ask(prompt).text

Esta é a garantia de contrato descrita em `LLMPort.ask_stream`: concatenar os fragmentos
do modo *stream* produz um texto consistente em conteúdo com o `Answer.text` do modo *run*.
O `FallbackLLM` (Composite) apenas repassa/materializa os fragmentos na ordem, sem alterar
conteúdo, então a propriedade do provider deve sobreviver ao envelopamento.

Estratégia de teste (Req 17.2/17.3):
- Provider fake **local** (sem rede), construído a partir de uma lista de fragmentos. Seu
  `ask` devolve `Answer(text="".join(chunks), ...)` e seu `ask_stream` emite cada fragmento
  na ordem — por construção, os dois modos são consistentes.
- Logger fake **no-op** (registra chamadas em memória, sem I/O).
- Timeouts generosos e `sleep` no-op, para que timing/backoff nunca interfiram: um provider
  que responde no SLA sempre vence na primeira tentativa.

Os fakes são definidos **localmente** neste arquivo de propósito (a consolidação em
`tests/fakes/` é responsabilidade da tarefa 22.1, que roda em paralelo — evitamos conflito
de escrita).

**Validates: Requirements 2.5**
"""

from __future__ import annotations

from typing import Any, Iterator, List

from hypothesis import given, settings
from hypothesis import strategies as st

from suporte.domain.ports import Answer
from suporte.infrastructure.llm.fallback_llm import FallbackLLM


# --------------------------------------------------------------------------- fakes ---
class ConsistentProvider:
    """`LLMPort` fake consistente: `ask` = concatenação do que `ask_stream` emite.

    Construído a partir de uma lista de fragmentos (`chunks`). Por construção, o texto
    do modo *run* (`ask().text`) é exatamente `"".join(chunks)`, e o modo *stream*
    (`ask_stream`) emite cada fragmento na ordem. Não faz nenhum acesso à rede.
    """

    def __init__(self, chunks: List[str], name: str = "consistent") -> None:
        self._chunks = list(chunks)
        self.name = name

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text="".join(self._chunks),
            provider=self.name,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        for chunk in self._chunks:
            yield chunk


class RecordingLogger:
    """Logger fake no-op que apenas grava as chamadas em memória (sem I/O)."""

    def __init__(self) -> None:
        self.records: List[dict] = []

    def log_request(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _make_fallback(chunks: List[str]) -> FallbackLLM:
    """Monta um `FallbackLLM` sobre um único provider consistente, sem interferência de timing.

    Timeouts generosos garantem que o provider responda dentro do SLA na primeira
    tentativa; `sleep` no-op evita qualquer espera real (o backoff nem chega a ser
    necessário, pois não há falhas).
    """
    provider = ConsistentProvider(chunks)
    return FallbackLLM(
        providers=[provider],
        per_attempt_timeout_s=1000.0,
        total_timeout_s=1000.0,
        max_attempts_per_provider=2,
        backoff_base_s=0.0,
        logger=RecordingLogger(),
        sleep=lambda _s: None,
    )


# ---------------------------------------------------------------------- propriedade ---
@given(chunks=st.lists(st.text()), prompt=st.text())
# deadline=None: cada exemplo cria um `ThreadPoolExecutor` (uma vez em `ask`, outra em
# `ask_stream`), cujo custo de agendamento varia bastante entre execuções. Isso é ruído de
# timing, não parte da propriedade sob teste (consistência de conteúdo run/stream); desligar
# o deadline evita falsos positivos de `DeadlineExceeded` sem afetar a asserção.
@settings(max_examples=200, deadline=None)
def test_prop7_run_stream_consistency(chunks: List[str], prompt: str) -> None:
    """Property 7: `"".join(ask_stream(prompt))` é consistente com `ask(prompt).text`.

    Para qualquer lista de fragmentos e qualquer prompt, o texto reconstruído a partir do
    modo *stream* do `FallbackLLM` coincide com o texto do modo *run*.

    **Validates: Requirements 2.5**
    """
    fallback = _make_fallback(chunks)

    run_text = fallback.ask(prompt).text
    stream_text = "".join(fallback.ask_stream(prompt))

    assert stream_text == run_text
    # E ambos coincidem com o conteúdo esperado do provider consistente.
    assert stream_text == "".join(chunks)
