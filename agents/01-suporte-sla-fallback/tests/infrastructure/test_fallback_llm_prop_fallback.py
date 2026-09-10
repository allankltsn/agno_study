"""Property-based test — Property 2: Fallback automático (RF-05).

Este arquivo cobre a **Property 2** do plano de testes do `FallbackLLM`:

    Property 2 — Fallback automático:
        Dado um provider primário `p1` que falha em TODAS as `max_attempts`
        tentativas e um provider secundário `p2` que responde, então `ask`:
          - retorna um `Answer` com `provider == "p2"`;
          - registra ao menos um evento de fallback (`used_fallback=True`);
          - o log de sucesso do `p2` traz `attempts == ["p1", "p2"]`,
            `used_fallback=True` e `result="ok"`.

A propriedade é exercitada com `hypothesis`, variando o `prompt`, o número de
tentativas por provider (`max_attempts_per_provider` de 1 a 3) e as contagens de
tokens da resposta do secundário. O `sleep` do backoff é substituído por um no-op
para que o teste seja rápido e determinístico (sem espera real).

**Validates: Requirements 5.2, 5.3, 5.4**

Os *fakes* usados aqui são definidos **localmente** neste módulo, de propósito:
os fakes compartilhados (`tests/fakes/`) são responsabilidade de outra tarefa que
roda em paralelo — mantê-los locais evita conflito de escrita entre subagentes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

from hypothesis import given, settings
from hypothesis import strategies as st

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm.fallback_llm import FallbackLLM


class AlwaysFails:
    """`LLMPort` fake cujo `ask` sempre falha com `ProviderUnavailable`.

    Representa um provider indisponível: cada tentativa levanta o erro de domínio
    contado como falha pelo `FallbackLLM`. Expõe `name` para nomear a tentativa nos
    logs e na lista `attempts`.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def ask(self, prompt: str) -> Answer:  # noqa: D401 - fake simples
        raise ProviderUnavailable(attempts=[self.name])

    def ask_stream(self, prompt: str) -> Iterator[str]:
        raise ProviderUnavailable(attempts=[self.name])


class AlwaysOk:
    """`LLMPort` fake cujo `ask` sempre responde com um `Answer` fixo.

    Representa um provider saudável: retorna uma `Answer` com `provider` igual ao
    seu `name` e `out_of_scope=False`. Expõe `name` para nomear a tentativa.
    """

    def __init__(
        self, name: str, prompt_tokens: int = 0, completion_tokens: int = 0
    ) -> None:
        self.name = name
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text="resposta de {}".format(self.name),
            provider=self.name,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield self.ask(prompt).text


class RecordingLogger:
    """Logger duplo que registra cada chamada a `log_request(**kwargs)`.

    Guarda os kwargs de cada chamada em `self.records`, na ordem de emissão, para
    que o teste possa asserir os eventos de fallback e o log de sucesso.
    """

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def log_request(self, **kwargs: Any) -> None:
        self.records.append(dict(kwargs))


@settings(max_examples=100)
@given(
    prompt=st.text(min_size=0, max_size=80),
    max_attempts=st.integers(min_value=1, max_value=3),
    prompt_tokens=st.integers(min_value=0, max_value=5000),
    completion_tokens=st.integers(min_value=0, max_value=5000),
)
def test_property2_fallback_automatico(
    prompt: str,
    max_attempts: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """p1 falha em todas as tentativas e p2 responde → ask retorna p2 + log de fallback.

    **Validates: Requirements 5.2, 5.3, 5.4**
    """
    logger = RecordingLogger()
    p1 = AlwaysFails("p1")
    p2 = AlwaysOk("p2", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    fallback = FallbackLLM(
        providers=[p1, p2],
        per_attempt_timeout_s=10.0,
        total_timeout_s=60.0,
        max_attempts_per_provider=max_attempts,
        backoff_base_s=0.01,
        logger=logger,
        sleep=lambda _seconds: None,  # backoff não dorme de verdade
    )

    answer = fallback.ask(prompt)

    # Req 5.2/5.3: fallback automático para o secundário, que passa a responder.
    assert answer.provider == "p2"
    assert answer.text == "resposta de p2"
    assert answer.out_of_scope is False

    # Req 5.4: pelo menos um evento de fallback registrado (used_fallback=True).
    fallback_events = [r for r in logger.records if r["used_fallback"] is True]
    assert fallback_events, "nenhum evento de fallback foi registrado"

    # Evento de esgotamento do primário: result="error" antes do sucesso do p2.
    exhausted_primary = [
        r
        for r in logger.records
        if r["result"] == "error" and r["provider"] == "p1"
    ]
    assert exhausted_primary, "esgotamento do provider primário não foi logado"
    assert exhausted_primary[0]["attempts"] == ["p1"]
    assert exhausted_primary[0]["used_fallback"] is True

    # Log de sucesso do secundário: última linha, com a ordem completa de tentativas.
    success = [r for r in logger.records if r["result"] == "ok"]
    assert len(success) == 1, "esperado exatamente um log de sucesso"
    success_record = success[0]
    assert success_record["provider"] == "p2"
    assert success_record["attempts"] == ["p1", "p2"]
    assert success_record["used_fallback"] is True
    assert success_record["prompt_tokens"] == prompt_tokens
    assert success_record["completion_tokens"] == completion_tokens


@settings(max_examples=50)
@given(
    prompt=st.text(min_size=0, max_size=80),
    max_attempts=st.integers(min_value=1, max_value=3),
)
def test_property2_all_fail_raises_provider_unavailable(
    prompt: str, max_attempts: int
) -> None:
    """Complemento: se p1 e p2 falham, `ask` levanta `ProviderUnavailable(["p1","p2"])`.

    Reforça a fronteira da Property 2 — o fallback é tentado na ordem e, esgotados
    todos, o contrato de erro comum é levantado com a lista ordenada de tentados.

    **Validates: Requirements 5.2, 5.3, 5.4**
    """
    logger = RecordingLogger()
    fallback = FallbackLLM(
        providers=[AlwaysFails("p1"), AlwaysFails("p2")],
        per_attempt_timeout_s=10.0,
        total_timeout_s=60.0,
        max_attempts_per_provider=max_attempts,
        backoff_base_s=0.01,
        logger=logger,
        sleep=lambda _seconds: None,
    )

    try:
        fallback.ask(prompt)
        raised = None
    except ProviderUnavailable as exc:
        raised = exc

    assert raised is not None, "esperado ProviderUnavailable quando todos falham"
    assert raised.attempts == ["p1", "p2"]
