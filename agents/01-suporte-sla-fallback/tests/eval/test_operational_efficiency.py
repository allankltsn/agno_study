"""Eficiência operacional — orçamentos derivados do log estruturado (Req 17.8–17.10).

Esta é a asserção de **eficiência operacional** da estratégia de testes (Property 14).
Ela NÃO instrumenta nada de novo: todas as métricas vêm exclusivamente do **log
estruturado por requisição** já emitido pelo `FallbackLLM` (`latency_ms`, tokens,
`used_fallback`), conforme Req 17.8. Um lote determinístico é executado, as linhas
de log JSON são capturadas e parseadas, agregadas, e comparadas a **orçamentos
(budgets)** configuráveis:

- **Latência** — p95 de `latency_ms` ≤ orçamento. A latência é sempre asserida por
  **percentil**, nunca por média nem por máximo (Req 17.8).
- **Tokens** — consumo **médio** de `total_tokens` por requisição ≤ orçamento (Req 17.9).
- **Taxa de fallback** — fração de requisições com `used_fallback=true` ≤ orçamento
  (Req 17.9).
- **SLA** — **nenhuma** `latency_ms` registrada excede `total_timeout_s` (Req 17.10).

Por que fakes (e por que fica no gate de PR):
    O lote usa os fakes reutilizáveis da `LLMPort` (`SlowProvider`/`ProgrammableAdapter`
    com `delay_s` conhecido) acionados por um `FakeClock` compartilhado — sem rede, sem
    `sleep` real e com latência **determinística**. Assim este teste é rápido e estável e
    compõe o gate obrigatório (`pytest -m "not e2e and not system and not integration"`),
    conforme o pipeline descrito no design. A camada de eficiência com LLM real/Ollama
    (nightly) reusa a MESMA lógica de agregação sobre o log — aqui apenas fixamos a fonte
    de tempo para tornar o resultado reprodutível.

Como a latência fica determinística:
    O `FallbackLLM` mede `latency_ms = (clock() - attempt_start) * 1000`. Como o
    `SlowProvider`/`ProgrammableAdapter` avança o `FakeClock` compartilhado em `delay_s`
    durante `ask`, a latência medida pelo `FallbackLLM` é exatamente `delay_s * 1000` para
    cada requisição — sem variância de parede.

Como o log é capturado:
    Injetamos um `logging.Logger` isolado (com um `StreamHandler` para um buffer em
    memória e formatter `%(message)s`) no `StructuredLogger` real. Cada requisição
    bem-sucedida do `FallbackLLM` emite uma linha JSON nesse buffer; o teste faz o parse
    e agrega. Usamos o `StructuredLogger` de produção de propósito — o objetivo é asserir
    sobre o log real, não sobre um duplo.
"""

from __future__ import annotations

import io
import json
import logging
import math
from typing import Any, Dict, List

import pytest

from suporte.infrastructure.llm.fallback_llm import FallbackLLM
from suporte.infrastructure.observability.logging import StructuredLogger

from tests.fakes.llm import FakeClock, ProgrammableAdapter, SlowProvider


# --------------------------------------------------------------------------- #
# Orçamentos (budgets) configuráveis
# --------------------------------------------------------------------------- #
# Orçamentos configuráveis (Req 17.9): trocar um número aqui reprova regressões de
# desempenho ao mudar modelo, prompt ou `provider_order`. Mantidos com folga sobre
# o comportamento determinístico do lote (ver `_run_batch`).
LATENCY_P95_BUDGET_MS = 250.0
"""Orçamento do p95 de `latency_ms` (ms). O lote determinístico tem p95 = 200 ms."""

MEAN_TOKENS_BUDGET = 6.0
"""Orçamento do consumo MÉDIO de `total_tokens` por requisição."""

FALLBACK_RATE_BUDGET = 0.30
"""Orçamento da taxa de `used_fallback=true` (fração de 0 a 1)."""

# Parâmetros do `FallbackLLM` para o lote. `total_timeout_s` é o teto do SLA (Req
# 17.10): nenhuma `latency_ms` registrada pode excedê-lo.
TOTAL_TIMEOUT_S = 25.0
PER_ATTEMPT_TIMEOUT_S = 10.0


# --------------------------------------------------------------------------- #
# Percentil — por posição, nunca média nem máximo (Req 17.8)
# --------------------------------------------------------------------------- #
def percentile(values: List[float], pct: float) -> float:
    """Calcula o percentil `pct` (0–100) de `values` por interpolação linear.

    Implementação local (sem dependências) do método "linear entre posições" —
    o mesmo do `numpy.percentile`/`statistics.quantiles` — para manter o teste
    autocontido. Usado para o p95 de latência: a latência é asserida por
    **percentil**, nunca por média nem por máximo (Req 17.8).

    Args:
        values: Amostras (ex.: `latency_ms` de cada requisição). Não pode ser vazia.
        pct: Percentil desejado, de 0 a 100 (ex.: `95.0` para o p95).

    Returns:
        O valor do percentil `pct`, interpolado entre as amostras ordenadas.

    Raises:
        ValueError: Se `values` estiver vazia.
    """
    if not values:
        raise ValueError("percentile() exige ao menos uma amostra")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    # Posição fracionária no vetor ordenado (0-based), método linear.
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


# --------------------------------------------------------------------------- #
# Captura do log estruturado real
# --------------------------------------------------------------------------- #
def _make_capturing_structured_logger() -> tuple[StructuredLogger, io.StringIO]:
    """Cria um `StructuredLogger` real que escreve as linhas JSON num buffer.

    Injeta no `StructuredLogger` de produção um `logging.Logger` isolado com um
    `StreamHandler` para um `io.StringIO` e formatter `%(message)s` — assim o
    buffer contém exatamente a(s) linha(s) JSON emitida(s), sem prefixos que
    quebrariam o parse. Nenhuma instrumentação adicional: é o mesmo caminho de log
    de produção (Req 17.8).

    Returns:
        A tupla `(structured_logger, buffer)`.
    """
    buffer = io.StringIO()
    handler = logging.StreamHandler(stream=buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Nome único por buffer: `getLogger` é singleton por nome; isolamos handlers
    # entre testes usando o id do buffer.
    logger = logging.getLogger("test.eval.operational.{}".format(id(buffer)))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return StructuredLogger(logger), buffer


def _parse_log_records(buffer: io.StringIO) -> List[Dict[str, Any]]:
    """Faz o parse das linhas JSON capturadas em uma lista de registros."""
    records: List[Dict[str, Any]] = []
    for line in buffer.getvalue().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# --------------------------------------------------------------------------- #
# Execução do lote determinístico (fakes com delay_s conhecido)
# --------------------------------------------------------------------------- #
def _run_batch() -> List[Dict[str, Any]]:
    """Executa um lote determinístico e devolve os registros de log parseados.

    Monta, para cada caso, um `FallbackLLM` com o `StructuredLogger` real e um
    `FakeClock` **novo** (para que a latência de cada requisição seja medida a
    partir do zero, refletindo apenas o `delay_s` do provider daquele caso). Os
    casos misturam:

    - respostas do provider **primário** (`used_fallback=false`), com latências
      conhecidas via `delay_s`;
    - um caso que **cai no fallback** (primário falha na primeira chamada e o
      secundário responde), exercitando a taxa de `used_fallback`.

    O `sleep` do backoff é substituído por um duplo que apenas avança o `FakeClock`,
    de modo que nenhuma espera real ocorre (gate-safe).

    Returns:
        Todos os registros JSON de resultado `ok` do lote, na ordem de execução.
    """
    # Lote (delay_s em segundos, fallback?). p95 dos latency_ms (em ms) = 200.
    # Latências: [50, 50, 50, 100, 100, 150, 150, 200, 200, 100(fallback)] ms.
    primary_delays_s = [0.05, 0.05, 0.05, 0.10, 0.10, 0.15, 0.15, 0.20, 0.20]
    fallback_delay_s = 0.10

    all_records: List[Dict[str, Any]] = []

    for delay_s in primary_delays_s:
        structured, buffer = _make_capturing_structured_logger()
        clock = FakeClock()
        # `sleep` que só avança o relógio virtual — sem espera de parede.
        fake_sleep = lambda seconds: clock.advance(seconds)  # noqa: E731
        provider = SlowProvider("openai", clock, delay_s=delay_s)
        fallback = FallbackLLM(
            providers=[provider],
            per_attempt_timeout_s=PER_ATTEMPT_TIMEOUT_S,
            total_timeout_s=TOTAL_TIMEOUT_S,
            max_attempts_per_provider=1,
            backoff_base_s=0.0,
            logger=structured,
            clock=clock,
            sleep=fake_sleep,
        )
        fallback.ask("pergunta do lote")
        all_records.extend(_parse_log_records(buffer))

    # Caso de fallback: primário falha na 1ª chamada, secundário responde. O log
    # de sucesso do secundário carrega `used_fallback=true` e latency do secundário.
    structured, buffer = _make_capturing_structured_logger()
    clock = FakeClock()
    fake_sleep = lambda seconds: clock.advance(seconds)  # noqa: E731
    primary = ProgrammableAdapter("openai", fail_always=True)
    secondary = SlowProvider("groq", clock, delay_s=fallback_delay_s)
    fallback = FallbackLLM(
        providers=[primary, secondary],
        per_attempt_timeout_s=PER_ATTEMPT_TIMEOUT_S,
        total_timeout_s=TOTAL_TIMEOUT_S,
        max_attempts_per_provider=1,
        backoff_base_s=0.0,
        logger=structured,
        clock=clock,
        sleep=fake_sleep,
    )
    fallback.ask("pergunta do lote")
    all_records.extend(_parse_log_records(buffer))

    # Só interessam as requisições respondidas (sucesso): são as que carregam
    # latência/tokens reais e o marcador de fallback do resultado.
    return [rec for rec in all_records if rec.get("result") == "ok"]


# --------------------------------------------------------------------------- #
# Testes
# --------------------------------------------------------------------------- #
class TestEficienciaOperacional:
    """Orçamentos de eficiência operacional sobre o log estruturado (Req 17.8–17.10)."""

    def test_p95_latencia_dentro_do_orcamento(self) -> None:
        """p95 de `latency_ms` ≤ orçamento — latência por percentil (Req 17.8)."""
        records = _run_batch()
        latencies = [float(rec["latency_ms"]) for rec in records]

        p95 = percentile(latencies, 95.0)

        assert p95 <= LATENCY_P95_BUDGET_MS, (
            "p95 de latency_ms {:.1f}ms excede o orçamento de {:.1f}ms".format(
                p95, LATENCY_P95_BUDGET_MS
            )
        )

    def test_media_de_tokens_dentro_do_orcamento(self) -> None:
        """Consumo MÉDIO de `total_tokens` por requisição ≤ orçamento (Req 17.9)."""
        records = _run_batch()
        total_tokens = [float(rec["total_tokens"]) for rec in records]

        mean_tokens = sum(total_tokens) / len(total_tokens)

        assert mean_tokens <= MEAN_TOKENS_BUDGET, (
            "média de tokens {:.2f} excede o orçamento de {:.2f}".format(
                mean_tokens, MEAN_TOKENS_BUDGET
            )
        )

    def test_taxa_de_fallback_dentro_do_orcamento(self) -> None:
        """Fração de requisições com `used_fallback=true` ≤ orçamento (Req 17.9)."""
        records = _run_batch()

        fallback_count = sum(1 for rec in records if rec["used_fallback"])
        fallback_rate = fallback_count / len(records)

        assert fallback_rate <= FALLBACK_RATE_BUDGET, (
            "taxa de fallback {:.2f} excede o orçamento de {:.2f}".format(
                fallback_rate, FALLBACK_RATE_BUDGET
            )
        )

    def test_nenhuma_latencia_excede_total_timeout(self) -> None:
        """Nenhuma `latency_ms` registrada excede `total_timeout_s` (Req 17.10)."""
        records = _run_batch()
        total_timeout_ms = TOTAL_TIMEOUT_S * 1000.0

        offenders = [
            float(rec["latency_ms"])
            for rec in records
            if float(rec["latency_ms"]) > total_timeout_ms
        ]

        assert not offenders, (
            "latências acima de total_timeout_s ({:.0f}ms): {}".format(
                total_timeout_ms, offenders
            )
        )

    def test_metricas_derivam_apenas_do_log_estruturado(self) -> None:
        """As métricas vêm só do log: cada registro traz os campos usados (Req 17.8)."""
        records = _run_batch()

        assert records, "o lote deve produzir ao menos uma requisição respondida"
        for rec in records:
            # Campos exclusivamente usados para derivar as métricas — sem
            # instrumentação adicional (Req 17.8).
            assert "latency_ms" in rec
            assert "total_tokens" in rec
            assert "used_fallback" in rec


class TestPercentileHelper:
    """Cobre o helper de percentil usado para asserir latência (Req 17.8)."""

    def test_percentil_de_amostra_unica(self) -> None:
        assert percentile([42.0], 95.0) == 42.0

    def test_p95_de_sequencia_conhecida(self) -> None:
        # De 1..10, o p95 (linear) fica entre 9 e 10.
        valores = [float(n) for n in range(1, 11)]
        resultado = percentile(valores, 95.0)
        assert 9.0 <= resultado <= 10.0

    def test_p50_e_a_mediana(self) -> None:
        assert percentile([10.0, 20.0, 30.0], 50.0) == 20.0

    def test_vazio_levanta(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 95.0)
