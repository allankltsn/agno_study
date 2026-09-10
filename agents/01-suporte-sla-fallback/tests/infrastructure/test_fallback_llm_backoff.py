"""Testes de UNIDADE do `FallbackLLM` — backoff exponencial e exaustão total.

Complementam os *property tests* (`test_fallback_llm_prop*.py`) com exemplos
determinísticos e específicos, focando três comportamentos do núcleo de
resiliência (Tarefa 8.6):

1. **Backoff exponencial entre tentativas de um mesmo provider** (Req 7.2): o
   `FallbackLLM` aguarda ``backoff_base_s * 2 ** (n - 1)`` antes de cada
   *re*-tentativa. Verificamos os intervalos EXATOS capturando o que o `sleep`
   injetado recebe — sem `sleep` real, sem rede (Req 17.3).
2. **Exaustão do último provider** (Req 7.3/7.4/5.5): esgotadas as tentativas do
   último provider da ordem, `ask` levanta `ProviderUnavailable` com `attempts`
   listando os providers na ORDEM em que foram tentados.
3. **Log de erro total** (Req 5.6): quando todos falham, o `StructuredLogger`
   registra um evento com a lista ordenada dos tentados, ``used_fallback=True`` e
   ``result="error"``.

Convenção (igual aos demais testes de `tests/infrastructure/`): duplos locais
autocontidos (`AlwaysFails`, `ProgrammableAdapter`, `RecordingLogger`,
`RecordingSleep`, `FakeClock`) — sem rede, sem `sleep` real. A consolidação de
fakes reutilizáveis em `tests/fakes/` é responsabilidade da tarefa 22.1.

**Validates: Requirements 5.5, 5.6, 7.2, 7.3, 7.4**
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

import pytest

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer
from suporte.infrastructure.llm.fallback_llm import FallbackLLM


# --------------------------------------------------------------------- fakes ---
class FakeClock:
    """Relógio monotônico virtual e controlável (callable), sem tempo real.

    Começa em `0.0` e só avança via `advance(seconds)`. Chamar a instância
    devolve o valor atual, como faria `time.monotonic()`. Nos testes de backoff
    usamos um `total_timeout_s` folgado para que o portão do SLA total nunca
    interfira — o foco é medir os intervalos de backoff, não o SLA.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)

    def __call__(self) -> float:
        return self._now


class RecordingSleep:
    """Duplo do `sleep` do backoff: registra os intervalos e AVANÇA o relógio.

    Em vez de dormir de verdade, guarda cada intervalo recebido em `self.calls`
    (para asserir a sequência exata de backoff) e avança o `FakeClock` associado
    pelo mesmo intervalo — assim o tempo virtual progride de forma consistente
    sem espera de parede (Req 17.3).
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: List[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


class AlwaysFails:
    """`LLMPort` fake cujo `ask`/`ask_stream` sempre falham com `ProviderUnavailable`.

    Cada chamada conta como uma tentativa consumida pelo `FallbackLLM`. Expõe
    `name` para nomear a tentativa em `attempts` e no log. Retorna imediatamente
    (levanta na hora), de modo que o `ThreadPoolExecutor` de `_call_with_timeout`
    resolve a future na hora e o timeout de parede nunca dispara.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        raise ProviderUnavailable(attempts=[self.name])

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.calls += 1
        raise ProviderUnavailable(attempts=[self.name])
        yield ""  # pragma: no cover - torna a função um gerador


class ProgrammableAdapter:
    """`LLMPort` fake que falha um nº configurável de vezes e então responde.

    Simula um provider com falhas transitórias: as primeiras `fail_times`
    chamadas a `ask` levantam `ProviderUnavailable`; a partir daí, responde com um
    `Answer` fixo (`provider` = `name`). É o duplo pedido pelo Req 17.2
    ("provider programável que falha N vezes antes de responder"), usado aqui para
    exercitar o backoff ENTRE as re-tentativas do mesmo provider.

    Args:
        name: Nome do provider (aparece em `Answer.provider` e em `attempts`).
        fail_times: Quantas chamadas iniciais devem falhar antes do sucesso.
    """

    def __init__(self, name: str, fail_times: int) -> None:
        self.name = name
        self._fail_times = fail_times
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderUnavailable(attempts=[self.name])
        return Answer(
            text="resposta de {}".format(self.name),
            provider=self.name,
            prompt_tokens=1,
            completion_tokens=2,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        answer = self.ask(prompt)
        yield answer.text


class RecordingLogger:
    """Logger duplo que registra cada chamada a `log_request(**kwargs)` na ordem."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def log_request(self, **kwargs: Any) -> None:
        self.records.append(dict(kwargs))


# ------------------------------------------------------- backoff exponencial ---
class TestBackoffExponencial:
    """O backoff entre re-tentativas de um provider segue base * 2 ** (n - 1) (Req 7.2)."""

    def test_backoff_aguarda_intervalos_exponenciais_ate_provider_responder(self) -> None:
        """3 falhas + 1 sucesso → backoff de [base, 2*base, 4*base] antes do sucesso.

        Um único provider programável falha 3 vezes e responde na 4ª tentativa.
        Entre a 1ª→2ª, 2ª→3ª e 3ª→4ª tentativas há um backoff; medimos os três
        intervalos exatos capturados pelo `sleep` injetado.

        **Validates: Requirements 7.2**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        provider = ProgrammableAdapter("primario", fail_times=3)

        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,  # folgado: SLA total não interfere aqui
            max_attempts_per_provider=4,
            backoff_base_s=0.5,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        answer = fallback.ask("pergunta")

        # Respondeu na 4ª tentativa do mesmo provider.
        assert answer.provider == "primario"
        assert provider.calls == 4
        # Três esperas de backoff, uma entre cada par de tentativas consecutivas:
        # n=1 → 0.5 * 2**0 = 0.5 ; n=2 → 0.5 * 2**1 = 1.0 ; n=3 → 0.5 * 2**2 = 2.0
        assert sleep.calls == [0.5, 1.0, 2.0]

    def test_sucesso_na_primeira_tentativa_nao_aplica_backoff(self) -> None:
        """Se o provider responde de primeira, nenhum backoff é aguardado.

        **Validates: Requirements 7.2**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        provider = ProgrammableAdapter("primario", fail_times=0)

        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=3,
            backoff_base_s=0.5,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        fallback.ask("pergunta")

        assert provider.calls == 1
        assert sleep.calls == []  # nenhum backoff

    def test_backoff_apenas_entre_tentativas_do_mesmo_provider_nao_apos_a_ultima(
        self,
    ) -> None:
        """Com max_attempts=2, um provider que sempre falha aplica 1 backoff (entre as 2).

        O backoff só ocorre ENQUANTO restam tentativas para o provider atual; após a
        última tentativa não há espera — passa-se ao próximo provider. Com um único
        provider e 2 tentativas, isso significa exatamente uma espera de `base`.

        **Validates: Requirements 7.2, 7.3**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        provider = AlwaysFails("solo")

        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=2,
            backoff_base_s=0.25,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable):
            fallback.ask("pergunta")

        assert provider.calls == 2
        # Uma única espera (entre a 1ª e a 2ª tentativa): 0.25 * 2**0 = 0.25.
        assert sleep.calls == [0.25]

    def test_backoff_base_zero_nao_dorme(self) -> None:
        """Com `backoff_base_s = 0`, nenhuma espera é realizada mesmo com re-tentativas.

        O intervalo calculado é 0; a implementação só chama `sleep` para intervalos
        positivos, então o duplo nunca é acionado.

        **Validates: Requirements 7.2**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        provider = AlwaysFails("solo")

        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=3,
            backoff_base_s=0.0,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable):
            fallback.ask("pergunta")

        assert provider.calls == 3
        assert sleep.calls == []  # base 0 → nenhuma espera


# ------------------------------------------------------- exaustão total --------
class TestExaustaoTotal:
    """Esgotados todos os providers, `ask` levanta `ProviderUnavailable` na ordem."""

    def test_exaustao_do_ultimo_provider_levanta_provider_unavailable_na_ordem(
        self,
    ) -> None:
        """Três providers que sempre falham → `ProviderUnavailable(["p1","p2","p3"])`.

        `attempts` deve preservar a ORDEM em que os providers foram tentados
        (Req 5.5), e cada provider deve ter sido tentado o número configurado de
        vezes antes do fallback para o próximo (Req 7.3/7.4).

        **Validates: Requirements 5.5, 7.3, 7.4**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        p1 = AlwaysFails("p1")
        p2 = AlwaysFails("p2")
        p3 = AlwaysFails("p3")

        fallback = FallbackLLM(
            providers=[p1, p2, p3],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=2,
            backoff_base_s=0.1,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable) as exc_info:
            fallback.ask("pergunta")

        # Ordem exata dos providers tentados (Req 5.5).
        assert exc_info.value.attempts == ["p1", "p2", "p3"]
        # Cada provider foi tentado até esgotar suas 2 tentativas (Req 7.3/7.4).
        assert p1.calls == 2
        assert p2.calls == 2
        assert p3.calls == 2

    def test_provider_unico_esgota_e_levanta_com_ordem_de_um_elemento(self) -> None:
        """Um só provider que falha → `ProviderUnavailable(["solo"])`.

        Fronteira: o "último" provider é também o primeiro; ao esgotar suas
        tentativas, a exaustão total é sinalizada com a lista de um elemento.

        **Validates: Requirements 5.5, 7.4**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()
        provider = AlwaysFails("solo")

        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=3,
            backoff_base_s=0.1,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable) as exc_info:
            fallback.ask("pergunta")

        assert exc_info.value.attempts == ["solo"]
        assert provider.calls == 3


# ------------------------------------------------------- log de erro total -----
class TestLogErroTotal:
    """Quando todos falham, o log final traz a ordem, used_fallback e result=error."""

    def test_log_de_erro_quando_todos_falham(self) -> None:
        """O log final tem `result="error"`, `used_fallback=True` e a ordem completa.

        Ao esgotar o último provider, o `FallbackLLM` emite um registro de erro que
        cobre a exaustão total (Req 5.6): a lista ordenada dos tentados,
        ``used_fallback=True`` e ``result="error"``.

        **Validates: Requirements 5.6**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()

        fallback = FallbackLLM(
            providers=[AlwaysFails("p1"), AlwaysFails("p2")],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=2,
            backoff_base_s=0.1,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable):
            fallback.ask("pergunta")

        # Nenhum log de sucesso deve existir quando todos falham.
        assert [r for r in logger.records if r["result"] == "ok"] == []

        # O ÚLTIMO registro é o de erro total: ordem completa, fallback e result.
        final = logger.records[-1]
        assert final["result"] == "error"
        assert final["used_fallback"] is True
        assert final["attempts"] == ["p1", "p2"]

    def test_log_de_erro_com_provider_unico(self) -> None:
        """Com um único provider que falha, há um log com `result="error"` na ordem [solo].

        Mesmo sem fallback real entre providers distintos, a exaustão total é
        registrada com a lista de tentados e `result="error"` (Req 5.6).

        **Validates: Requirements 5.6**
        """
        clock = FakeClock()
        sleep = RecordingSleep(clock)
        logger = RecordingLogger()

        fallback = FallbackLLM(
            providers=[AlwaysFails("solo")],
            per_attempt_timeout_s=10.0,
            total_timeout_s=10_000.0,
            max_attempts_per_provider=2,
            backoff_base_s=0.1,
            logger=logger,
            clock=clock,
            sleep=sleep,
        )

        with pytest.raises(ProviderUnavailable):
            fallback.ask("pergunta")

        error_logs = [r for r in logger.records if r["result"] == "error"]
        assert error_logs, "esperado ao menos um log de erro na exaustão total"
        final = error_logs[-1]
        assert final["attempts"] == ["solo"]
        assert final["result"] == "error"
        assert final["used_fallback"] is True
