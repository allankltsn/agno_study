"""Property-based test — Property 4: SLA total nunca excedido (RNF-01 / Req 6.5).

Este arquivo cobre a **Property 4** do plano de testes do `FallbackLLM`:

    Property 4 — SLA total:
        Com um `clock` injetado (virtual) e providers que apenas FALHAM
        (`AlwaysFails`) enquanto consomem tempo virtual, o tempo total decorrido
        até `ask` levantar `ProviderUnavailable` NÃO excede `total_timeout_s`
        além da margem de UMA verificação de tempo — ou seja, no máximo o custo de
        uma tentativa adicional (o avanço de relógio de uma chamada a `ask`).
        Além disso, nenhuma NOVA tentativa é iniciada depois que o orçamento total
        é atingido.

Como o SLA total é o comportamento sob teste, ele é exercitado de forma
**determinística** com um relógio virtual (`FakeClock`), sem `sleep` real e sem
espera de parede (Req 17.3):

- `clock` recebe um `FakeClock` controlável.
- `sleep` (backoff) é um duplo que apenas AVANÇA o `FakeClock` pelo intervalo de
  backoff pedido — assim o backoff consome tempo *virtual*, não real.
- Cada provider `AlwaysFails` AVANÇA o `FakeClock` por um valor configurável a cada
  chamada a `ask` (simulando o tempo gasto na tentativa) e então levanta
  `ProviderUnavailable`.

Adaptação sobre o `ThreadPoolExecutor` (timeout por tentativa):
    O `FallbackLLM._call_with_timeout` usa um `ThreadPoolExecutor` real com
    `future.result(timeout=deadline)`, cujo `deadline` é medido em tempo de PAREDE,
    não no `clock` injetado. Para testar o portão do SLA TOTAL (que é checado via
    `clock()`) sem espera real, os providers deste teste RETORNAM/LEVANTAM
    imediatamente — a `future` resolve instantaneamente, então o timeout de parede
    nunca dispara. O avanço do tempo é aplicado no relógio *virtual* dentro de
    `ask`, e `per_attempt_timeout_s` é mantido grande o bastante para nunca competir
    com o orçamento virtual. Assim, o teste é rápido e o portão exercitado é
    exclusivamente o do SLA total (`clock`), como pretende a Property 4.

**Validates: Requirements 6.5**

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


class FakeClock:
    """Relógio monotônico virtual e controlável (callable), sem tempo real.

    Substitui `time.monotonic` no `FallbackLLM`. Começa em `0.0` e só avança quando
    `advance(seconds)` é chamado — seja pelos providers (simulando o tempo de uma
    tentativa) ou pelo `sleep` do backoff (duplo que avança o relógio). Chamar a
    instância (`__call__`) devolve o valor atual, como faria `time.monotonic()`.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def advance(self, seconds: float) -> None:
        """Avança o tempo virtual em `seconds` (>= 0)."""
        self._now += float(seconds)

    def __call__(self) -> float:
        return self._now


class AlwaysFails:
    """`LLMPort` fake que AVANÇA o relógio virtual e então falha.

    Cada chamada a `ask` (uma tentativa) avança o `FakeClock` injetado por
    `advance_per_call` segundos — simulando o tempo consumido pela tentativa — e em
    seguida levanta `ProviderUnavailable`, o contrato de falha contado pelo
    `FallbackLLM`. Também conta quantas vezes `ask` foi chamado, para asserir que
    NENHUMA nova tentativa é iniciada após o orçamento total ser atingido.

    Retorna/levanta imediatamente (sem espera de parede), de modo que o
    `ThreadPoolExecutor` de `_call_with_timeout` resolve a future na hora e o timeout
    de parede nunca dispara — o único portão exercitado é o do SLA total (`clock`).
    """

    def __init__(self, name: str, clock: FakeClock, advance_per_call: float) -> None:
        self.name = name
        self._clock = clock
        self._advance_per_call = advance_per_call
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        self._clock.advance(self._advance_per_call)
        raise ProviderUnavailable(attempts=[self.name])

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.calls += 1
        self._clock.advance(self._advance_per_call)
        raise ProviderUnavailable(attempts=[self.name])


class RecordingLogger:
    """Logger duplo que registra cada chamada a `log_request(**kwargs)`.

    Guarda os kwargs de cada chamada em `self.records`, na ordem de emissão, para
    inspeção nos testes (aqui usado para confirmar o log de erro final).
    """

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def log_request(self, **kwargs: Any) -> None:
        self.records.append(dict(kwargs))


@settings(max_examples=200, deadline=None)
@given(
    prompt=st.text(min_size=0, max_size=60),
    total_timeout_s=st.floats(min_value=0.5, max_value=30.0),
    advance_per_call=st.floats(min_value=0.1, max_value=8.0),
    max_attempts=st.integers(min_value=1, max_value=4),
    n_providers=st.integers(min_value=1, max_value=4),
    backoff_base_s=st.floats(min_value=0.0, max_value=1.0),
)
def test_property4_sla_total_nunca_excedido(
    prompt: str,
    total_timeout_s: float,
    advance_per_call: float,
    max_attempts: int,
    n_providers: int,
    backoff_base_s: float,
) -> None:
    """O tempo virtual até `ProviderUnavailable` não excede o SLA além de 1 avanço.

    Com providers que só falham (avançando o relógio virtual a cada tentativa) e um
    backoff que também consome tempo virtual, `ask` deve interromper NOVAS
    tentativas assim que o orçamento total é atingido. O tempo virtual decorrido no
    momento da falha total fica limitado a `total_timeout_s` mais a margem de uma
    única verificação — modelada aqui como o avanço de UMA tentativa adicional
    (a última tentativa iniciada enquanto ainda havia orçamento pode ultrapassar o
    teto ao consumir seu próprio tempo).

    **Validates: Requirements 6.5**
    """
    clock = FakeClock()
    logger = RecordingLogger()
    providers = [
        AlwaysFails("p{}".format(i), clock=clock, advance_per_call=advance_per_call)
        for i in range(n_providers)
    ]

    fallback = FallbackLLM(
        providers=providers,
        # per_attempt grande: o portão de parede nunca compete com o virtual.
        per_attempt_timeout_s=1000.0,
        total_timeout_s=total_timeout_s,
        max_attempts_per_provider=max_attempts,
        backoff_base_s=backoff_base_s,
        logger=logger,
        clock=clock,
        # backoff consome tempo VIRTUAL avançando o FakeClock (sem sleep real).
        sleep=clock.advance,
    )

    start = clock()

    try:
        fallback.ask(prompt)
        raised = None
    except ProviderUnavailable as exc:
        raised = exc

    # Todos os providers só falham: a requisição SEMPRE termina em erro.
    assert raised is not None, "esperado ProviderUnavailable quando todos falham"

    elapsed = clock() - start

    # Margem de UMA verificação: a última tentativa que iniciou dentro do orçamento
    # ainda pode consumir seu próprio avanço, ultrapassando o teto em no máximo o
    # custo de uma tentativa adicional. Uma folga epsilon cobre erro de ponto
    # flutuante.
    margin = advance_per_call + 1e-9
    assert elapsed <= total_timeout_s + margin, (
        "SLA total excedido além da margem de uma verificação: "
        "elapsed={:.6f} > total_timeout_s={:.6f} + margin={:.6f}".format(
            elapsed, total_timeout_s, margin
        )
    )

    # Nenhuma NOVA tentativa é iniciada após o orçamento total ser atingido: o portão
    # é checado ANTES de cada tentativa, então quando o relógio já bateu o teto,
    # nenhuma chamada extra a `ask` acontece. O total de chamadas nunca ultrapassa o
    # máximo teórico de tentativas (nº de providers x tentativas por provider).
    total_calls = sum(p.calls for p in providers)
    assert total_calls <= n_providers * max_attempts

    # O log de erro final foi emitido (result="error") — Req 6.5 / 5.6.
    error_logs = [r for r in logger.records if r["result"] == "error"]
    assert error_logs, "esperado ao menos um log de erro na exaustão total"


@settings(max_examples=50, deadline=None)
@given(
    total_timeout_s=st.floats(min_value=1.0, max_value=20.0),
    advance_per_call=st.floats(min_value=1.0, max_value=5.0),
)
def test_property4_stops_before_starting_attempt_over_budget(
    total_timeout_s: float, advance_per_call: float
) -> None:
    """O portão do SLA interrompe ANTES de iniciar uma tentativa fora do orçamento.

    Com muitas tentativas disponíveis e providers que avançam o relógio a cada
    falha, o número de tentativas efetivamente iniciadas fica limitado ao que cabe
    no orçamento total: assim que `clock() - start >= total_timeout_s`, nenhuma nova
    tentativa começa. Confirmamos que o número de tentativas iniciadas não excede o
    limite derivado do orçamento (uma a mais que o piso, pela margem de uma
    verificação).

    **Validates: Requirements 6.5**
    """
    clock = FakeClock()
    logger = RecordingLogger()
    # Um único provider com muitas tentativas, sem backoff, para isolar o portão.
    provider = AlwaysFails("solo", clock=clock, advance_per_call=advance_per_call)

    fallback = FallbackLLM(
        providers=[provider],
        per_attempt_timeout_s=1000.0,
        total_timeout_s=total_timeout_s,
        max_attempts_per_provider=1000,
        backoff_base_s=0.0,
        logger=logger,
        clock=clock,
        sleep=clock.advance,
    )

    try:
        fallback.ask("pergunta")
    except ProviderUnavailable:
        pass

    # Cada tentativa avança `advance_per_call`. O portão para de iniciar tentativas
    # quando o tempo decorrido >= total_timeout_s. Logo o nº de tentativas iniciadas
    # é, no máximo, o teto do orçamento dividido pelo avanço, mais uma pela margem.
    import math

    max_started = math.floor(total_timeout_s / advance_per_call) + 1
    assert provider.calls <= max_started, (
        "iniciou {} tentativas; esperado no máximo {} dado o orçamento".format(
            provider.calls, max_started
        )
    )
