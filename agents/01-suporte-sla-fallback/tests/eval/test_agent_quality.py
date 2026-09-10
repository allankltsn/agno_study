"""Harness de eval de eficácia — golden set + scorers, agregado por taxa (camada 2).

Esta é a **camada 2** da estratégia de testes (comportamento do agente com LLM real ou
Ollama). Diferente do gate de PR — rápido, determinístico, com fakes e sem rede — aqui
montamos o caso de uso **real** via ``composition_root.build_answer_question()`` e o
exercitamos contra um provider de LLM concreto sobre o golden set versionado
(``tests/eval/dataset.jsonl``).

Por que fica fora do gate de PR
-------------------------------
O módulo inteiro é marcado ``@pytest.mark.e2e`` (via ``pytestmark`` de módulo), de modo que
o gate de PR o **exclui** com ``pytest -m "not e2e and not system and not integration"``
(Req 17.1, 17.11). Ele é lento (rede/inferência) e **não-determinístico** (depende do
modelo), então roda em *nightly*/pré-release, nunca por commit.

Como a eficácia é medida (por TAXA, nunca pass/fail por caso) — Req 17.5, 17.6
------------------------------------------------------------------------------
Executamos cada caso do dataset pelo caso de uso real, aplicamos o
``score_deterministic`` (scorers da tarefa 23.2) e agregamos ``passed`` como uma **taxa**
comparada a um threshold — jamais reprovando por um caso individual (Req 17.5):

- ``test_acuracia_geral``: acurácia geral (fração de **todos** os casos aprovados) ≥
  ``ACCURACY_THRESHOLD``.
- ``test_recall_out_of_scope``: *out_of_scope recall* (fração de casos ``out_of_scope`` que
  resultaram em ``escalated=True``) ≥ ``OUT_OF_SCOPE_RECALL_THRESHOLD``, um threshold
  **estritamente mais alto** que o da acurácia geral — alucinar onde deveria escalar é o
  pior erro do agente (Req 3, Req 17.6).

Provider: Ollama por padrão, real só em pré-release — Req 17.12
---------------------------------------------------------------
O harness roda com **Ollama** por padrão (custo zero, sem rede externa), selecionado só
por ``MODEL_PROVIDER_ORDER`` — trocar para um provider real (pré-release) é mudar essa
variável de ambiente, sem tocar em domínio nem em aplicação, pois a ``LLMPort`` é a mesma
(Req 17.12, Req 4.2). Nada de provider é embutido no código do teste.

Nunca bloqueia sem backend
--------------------------
Antes de exercitar o modelo, o harness verifica se há um backend utilizável (chave real
presente **ou** Ollama alcançável). Quando não há nenhum — o caso comum ao rodar
``pytest -m e2e`` numa máquina sem Ollama e sem chaves — cada teste faz ``pytest.skip`` e,
portanto, **pula** em vez de reprovar. Assim a suíte marcada ``e2e`` continua segura de
rodar em qualquer ambiente.

Estabilidade estatística — Req 17.14
------------------------------------
O ambiente de eval fixa uma **temperatura de sampling baixa** (``EVAL_TEMPERATURE``, via a
env var que o SDK do provider lê) e os thresholds são definidos **com folga**, de modo que
o ruído estatístico do modelo não reprove a suíte de forma intermitente.

Registro por execução (detecção de regressão)
---------------------------------------------
Cada execução registra um resumo (taxas medidas, thresholds, provider, contagem de casos)
tanto no ``print`` capturado pelo pytest quanto num arquivo ``.jsonl`` append-only
(``tests/eval/.results/agent_quality.jsonl``, ignorado pelo Git), para comparar execuções
ao longo do tempo e detectar regressão de eficácia.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

import pytest

from suporte.application.dto import AnswerRequest, Mode
from suporte.composition_root import build_answer_question
from suporte.infrastructure.config import default_provider_registry

from tests.eval.scorers import DeterministicScore, score_deterministic

# Todo o módulo pertence à camada `e2e` (comportamento com LLM real/Ollama). O gate de PR
# o exclui com ``pytest -m "not e2e and not system and not integration"`` (Req 17.1, 17.11).
pytestmark = pytest.mark.e2e


# --------------------------------------------------------------------------- #
# Thresholds (com folga) e temperatura baixa — Req 17.6, 17.14
# --------------------------------------------------------------------------- #
# Thresholds definidos COM FOLGA para não reprovar por ruído estatístico do modelo
# (Req 17.14). O recall de out_of_scope é ESTRITAMENTE MAIOR que a acurácia geral
# (Req 17.6): alucinar onde deveria escalar é o pior erro do agente (Req 3).
ACCURACY_THRESHOLD = 0.70
"""Piso da acurácia geral (fração de TODOS os casos aprovados pelo scorer determinístico)."""

OUT_OF_SCOPE_RECALL_THRESHOLD = 0.90
"""Piso do out_of_scope recall — estritamente MAIOR que a acurácia geral (Req 17.6)."""

# Sanidade em tempo de import: o contrato do Req 17.6 (recall > acurácia) é estrutural.
assert OUT_OF_SCOPE_RECALL_THRESHOLD > ACCURACY_THRESHOLD, (
    "Req 17.6: o threshold de out_of_scope recall deve ser estritamente maior "
    "que o da acurácia geral."
)

# Temperatura de sampling BAIXA fixada para o ambiente de eval (Req 17.14). É aplicada por
# variável de ambiente que o SDK do provider lê — sem tocar no código do agente.
EVAL_TEMPERATURE = "0.0"
_TEMPERATURE_ENV = "LLM_TEMPERATURE"

# Ordem de providers padrão do harness: Ollama primeiro (custo zero, sem rede externa),
# trocável só por env em pré-release (Req 17.12). Não embutimos provider no teste além
# deste default sobrescrevível.
_DEFAULT_EVAL_PROVIDER_ORDER = "ollama"

# Endpoint padrão do Ollama quando `OLLAMA_HOST` não está definido (só usado para o teste
# de alcançabilidade do backend; o endereço real do agente vem sempre de `OLLAMA_HOST`).
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Arquivo de registro append-only por execução (ignorado pelo Git via `.gitignore`).
_RESULTS_DIR = Path(__file__).with_name(".results")
_RESULTS_FILE = _RESULTS_DIR / "agent_quality.jsonl"

# Chaves reais que habilitam um provider de pré-release (só a PRESENÇA é verificada,
# nunca o valor — Req 10.x).
_REAL_KEY_ENVS: Tuple[str, ...] = ("OPENAI_API_KEY", "GROQ_API_KEY")


# --------------------------------------------------------------------------- #
# Carregamento do golden set
# --------------------------------------------------------------------------- #
_DATASET_PATH = Path(__file__).with_name("dataset.jsonl")


def _load_dataset() -> List[Dict[str, Any]]:
    """Carrega o golden set versionado (`dataset.jsonl`), uma linha JSON por caso.

    Ignora linhas em branco (tolerância a formatação do arquivo). Cada caso traz ao menos
    ``question`` e ``expected`` (``in_scope``/``out_of_scope``); casos ``in_scope`` trazem
    âncoras ``must_mention``.

    Returns:
        A lista de casos como dicionários, na ordem do arquivo.

    Raises:
        AssertionError: Se o dataset tiver menos de 20 casos (Req 17.4 exige ≥ 20) — o que
            indicaria um dataset corrompido/incompleto.
    """
    cases: List[Dict[str, Any]] = []
    with _DATASET_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    assert len(cases) >= 20, (
        f"golden set deve ter ≥ 20 casos (Req 17.4); encontrados {len(cases)}"
    )
    return cases


# --------------------------------------------------------------------------- #
# Seleção/checagem de backend — nunca bloqueia sem provider utilizável
# --------------------------------------------------------------------------- #
def _tcp_reachable(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """Verifica se `host:port` aceita conexão TCP (checagem barata de alcançabilidade).

    Usada apenas para decidir *pular* o teste quando o Ollama não está no ar — não faz
    nenhuma inferência. Qualquer erro de socket é tratado como "não alcançável".

    Args:
        host: Host a testar (ex.: ``"localhost"``).
        port: Porta a testar (ex.: ``11434`` do Ollama).
        timeout_s: Timeout da tentativa de conexão, em segundos.

    Returns:
        ``True`` se a conexão TCP foi aceita; ``False`` caso contrário.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _ollama_reachable() -> bool:
    """Indica se o servidor Ollama parece alcançável no endpoint configurado.

    Lê o endpoint de ``OLLAMA_HOST`` (ou usa o default local só para a checagem) e testa a
    conexão TCP no host/porta correspondentes. É uma heurística barata para *pular* o teste
    quando não há Ollama — não valida se um modelo está carregado.

    Returns:
        ``True`` quando o host/porta do Ollama aceita conexão; ``False`` caso contrário.
    """
    raw = os.environ.get("OLLAMA_HOST") or _DEFAULT_OLLAMA_HOST
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    return _tcp_reachable(host, port)


def _configured_provider_order() -> List[str]:
    """Resolve a ordem de providers do eval (env `MODEL_PROVIDER_ORDER`, default Ollama).

    O harness roda com Ollama por padrão; em pré-release a ordem é trocada só por
    ``MODEL_PROVIDER_ORDER`` (Req 17.12). Retorna a lista de nomes, minúsculos e sem itens
    vazios.

    Returns:
        A lista de nomes de provider na ordem configurada.
    """
    raw = os.environ.get("MODEL_PROVIDER_ORDER") or _DEFAULT_EVAL_PROVIDER_ORDER
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _usable_backend_or_skip() -> str:
    """Garante um backend utilizável para o eval, ou pula o teste (nunca reprova).

    Regra de decisão, coerente com o Req 17.12 (Ollama por padrão, real só em pré-release):

    - Se a ordem configurada inclui ``ollama`` e o servidor Ollama está alcançável, usa
      Ollama.
    - Senão, se algum provider real está na ordem **e** sua chave está presente, usa esse
      provider real (caminho de pré-release).
    - Caso contrário, faz ``pytest.skip`` — de modo que a suíte ``e2e`` **pula** em vez de
      reprovar quando não há Ollama nem chaves (ex.: máquina de dev limpa rodando
      ``pytest -m e2e``).

    Returns:
        O nome do provider selecionado (ex.: ``"ollama"``, ``"openai"``).

    Raises:
        Skipped: Via `pytest.skip` quando nenhum backend está utilizável.
    """
    order = _configured_provider_order()
    known_real = set(default_provider_registry())  # {"openai", "groq"}

    if "ollama" in order and _ollama_reachable():
        return "ollama"

    for name in order:
        if name in known_real:
            key_env = default_provider_registry()[name].api_key_env
            if os.environ.get(key_env):
                return name

    pytest.skip(
        "Nenhum backend de eval utilizável: Ollama não alcançável e nenhuma chave real "
        f"presente ({', '.join(_REAL_KEY_ENVS)}). Eval de eficácia (e2e) pulado."
    )


# --------------------------------------------------------------------------- #
# Fixture: ambiente de eval (provider + temperatura baixa) e execução do dataset
# --------------------------------------------------------------------------- #
@pytest.fixture()
def eval_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Prepara o ambiente de eval: seleciona o backend e fixa temperatura baixa.

    - Escolhe um backend utilizável (ou pula o teste) e o fixa em ``MODEL_PROVIDER_ORDER``
      **apenas por env** (Req 4.2, 17.12).
    - Fixa uma temperatura de sampling baixa via ``LLM_TEMPERATURE`` (Req 17.14), lida pelo
      SDK do provider — sem tocar no código do agente.

    Args:
        monkeypatch: Fixture do pytest para ajustar env vars com escopo do teste.

    Returns:
        O nome do provider selecionado para esta execução.
    """
    provider = _usable_backend_or_skip()
    monkeypatch.setenv("MODEL_PROVIDER_ORDER", provider)
    monkeypatch.setenv(_TEMPERATURE_ENV, EVAL_TEMPERATURE)
    return provider


def _run_dataset(provider: str) -> Tuple[List[Tuple[Mapping[str, Any], DeterministicScore]], str]:
    """Executa TODO o golden set pelo caso de uso real e pontua cada caso.

    Monta o caso de uso via ``build_answer_question()`` (que lê o provider de
    ``MODEL_PROVIDER_ORDER``) e, para cada caso, chama ``execute`` em modo *run*,
    aplicando ``score_deterministic``. Se a montagem falhar por ausência de backend
    utilizável (ex.: Ollama não registrado/indisponível), **pula** em vez de reprovar,
    preservando a garantia de que a suíte ``e2e`` nunca bloqueia sem provider.

    Args:
        provider: Nome do provider selecionado (para o resumo de registro).

    Returns:
        Uma tupla ``(scored, provider)`` onde ``scored`` é a lista de pares
        ``(caso, DeterministicScore)`` na ordem do dataset.

    Raises:
        Skipped: Se o caso de uso não puder ser montado por falta de backend utilizável.
    """
    cases = _load_dataset()
    try:
        use_case = build_answer_question()
    except (RuntimeError, ValueError) as exc:
        # Montagem falhou (ex.: provider fora do registro, chave ausente): tratamos como
        # "sem backend utilizável" e pulamos — nunca reprovamos a suíte e2e por isso.
        pytest.skip(f"Não foi possível montar o agente de eval ({provider}): {exc}")

    scored: List[Tuple[Mapping[str, Any], DeterministicScore]] = []
    for case in cases:
        response = use_case.execute(
            AnswerRequest(question=str(case.get("question", "")), mode=Mode.RUN)
        )
        scored.append((case, score_deterministic(case, response)))
    return scored, provider


# --------------------------------------------------------------------------- #
# Agregação por taxa
# --------------------------------------------------------------------------- #
def _overall_accuracy(
    scored: List[Tuple[Mapping[str, Any], DeterministicScore]]
) -> float:
    """Acurácia geral: fração de TODOS os casos aprovados pelo scorer determinístico."""
    if not scored:
        return 0.0
    passed = sum(1 for _, score in scored if score.passed)
    return passed / len(scored)


def _out_of_scope_recall(
    scored: List[Tuple[Mapping[str, Any], DeterministicScore]]
) -> Optional[float]:
    """out_of_scope recall: fração de casos ``out_of_scope`` que escalaram (Req 17.6).

    Como o ``score_deterministic`` aprova um caso ``out_of_scope`` exatamente quando
    ``escalated=True``, a taxa de aprovação restrita a esses casos É o recall de escalonamento.

    Returns:
        O recall (0–1), ou ``None`` se não houver casos ``out_of_scope`` no dataset.
    """
    oos = [
        score
        for case, score in scored
        if str(case.get("expected", case.get("expects", ""))).strip().lower()
        == "out_of_scope"
    ]
    if not oos:
        return None
    escalated = sum(1 for score in oos if score.passed)
    return escalated / len(oos)


# --------------------------------------------------------------------------- #
# Registro por execução (detecção de regressão)
# --------------------------------------------------------------------------- #
def _record_run(summary: Dict[str, Any]) -> None:
    """Registra o resumo desta execução para comparar taxas ao longo do tempo.

    Escreve no ``print`` (capturado pelo pytest, visível com ``-s``/em falha) e faz *append*
    de uma linha JSON num arquivo local ignorado pelo Git. Falhas de escrita em disco são
    silenciadas — o registro é um auxílio de observação, nunca deve reprovar o eval.

    Args:
        summary: Dicionário serializável com métricas medidas, thresholds, provider e
            contagem de casos.
    """
    print("[eval:agent_quality] " + json.dumps(summary, ensure_ascii=False))
    try:
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with _RESULTS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except OSError:
        # Registro é best-effort: nunca deve reprovar o eval por falha de I/O.
        pass


def _base_summary(provider: str) -> Dict[str, Any]:
    """Monta os campos comuns do resumo de registro (timestamp UTC, provider, thresholds)."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "temperature": EVAL_TEMPERATURE,
        "accuracy_threshold": ACCURACY_THRESHOLD,
        "out_of_scope_recall_threshold": OUT_OF_SCOPE_RECALL_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# Testes (gate por TAXA, nunca por caso individual — Req 17.5)
# --------------------------------------------------------------------------- #
def test_acuracia_geral(eval_env: str) -> None:
    """Acurácia geral do agente sobre o golden set ≥ threshold (Req 17.5).

    Executa TODO o dataset pelo caso de uso real, agrega a fração de casos aprovados pelo
    scorer determinístico e compara ao ``ACCURACY_THRESHOLD`` — nunca reprovando por um
    caso individual. Registra o resultado para detecção de regressão.

    **Validates: Requirements 17.5, 17.12, 17.14**
    """
    scored, provider = _run_dataset(eval_env)
    accuracy = _overall_accuracy(scored)

    summary = _base_summary(provider)
    summary.update(
        {
            "test": "acuracia_geral",
            "total_cases": len(scored),
            "passed_cases": sum(1 for _, s in scored if s.passed),
            "accuracy": round(accuracy, 4),
            "failures": [
                {"id": case.get("id"), "reason": score.reason}
                for case, score in scored
                if not score.passed
            ],
        }
    )
    _record_run(summary)

    assert accuracy >= ACCURACY_THRESHOLD, (
        "acurácia geral {:.2%} abaixo do threshold de {:.2%} "
        "(provider={})".format(accuracy, ACCURACY_THRESHOLD, provider)
    )


def test_recall_out_of_scope(eval_env: str) -> None:
    """out_of_scope recall ≥ threshold (mais alto que a acurácia geral) — Req 17.6.

    Restringe a agregação aos casos ``out_of_scope`` e exige que a fração que escalou
    (``escalated=True``) atinja o ``OUT_OF_SCOPE_RECALL_THRESHOLD`` — estritamente maior que
    o da acurácia geral, pois alucinar onde deveria escalar é o pior erro do agente
    (Req 3, Req 17.6). Agregado por taxa, nunca por caso. Registra o resultado.

    **Validates: Requirements 17.5, 17.6, 17.12, 17.14**
    """
    scored, provider = _run_dataset(eval_env)
    recall = _out_of_scope_recall(scored)

    assert recall is not None, "golden set deve conter casos out_of_scope (Req 17.4)"

    oos_total = sum(
        1
        for case, _ in scored
        if str(case.get("expected", case.get("expects", ""))).strip().lower()
        == "out_of_scope"
    )
    summary = _base_summary(provider)
    summary.update(
        {
            "test": "recall_out_of_scope",
            "out_of_scope_cases": oos_total,
            "out_of_scope_recall": round(recall, 4),
            "not_escalated": [
                case.get("id")
                for case, score in scored
                if str(case.get("expected", case.get("expects", ""))).strip().lower()
                == "out_of_scope"
                and not score.passed
            ],
        }
    )
    _record_run(summary)

    assert recall >= OUT_OF_SCOPE_RECALL_THRESHOLD, (
        "out_of_scope recall {:.2%} abaixo do threshold de {:.2%} "
        "(provider={})".format(recall, OUT_OF_SCOPE_RECALL_THRESHOLD, provider)
    )
