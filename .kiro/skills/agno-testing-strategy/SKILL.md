---
name: agno-testing-strategy
description: Estrategia de testes automatizados e e2e para agentes Agno v2 - as tres camadas de teste (transporte deterministico, comportamento do agente, sistema real), eficiencia operacional (latencia/tokens/fallback como assercao sobre o log estruturado), eval de eficacia com golden set e scorers (deterministico + LLM-as-judge), resiliencia deterministica (timeout/retry/fallback com clock e adapters fake), harness em pytest, marcadores e pipeline de CI. Use ao escrever, organizar ou revisar testes de qualquer agente Agno do workspace (CLI, FastAPI, Lambda, AgentOS).
---

# Estrategia de testes para agentes Agno (unidade, e2e e eval)

Guia obrigatorio de testes para **todos os projetos de agente** deste workspace. Define o
que testar, como automatizar e como transformar cada teste num gate objetivo (pass/fail),
nao numa metrica solta. Serve para agentes entregues como CLI, funcao chamavel, FastAPI +
API Gateway e AgentOS.

Le junto de `agno-observability` (o log estruturado e a base das assercoes de eficiencia),
`agno-project-architecture-solid` (as camadas ports & adapters que tornam o agente
testavel) e `agno-agentos-eval-deploy` (inspecao manual de traces/sessions na UI, que este
guia automatiza).

## Principio central: separe "eficiencia" em tres sentidos

A palavra "eficiencia" de um agente e ambigua. Cada sentido pede um teste diferente e um
lugar diferente no pipeline. Nunca misture os tres numa suite so.

| Sentido | O que mede | Determinismo | Onde roda |
|---------|-----------|--------------|-----------|
| **Operacional** | latencia (p95), tokens/custo, taxa de fallback | Alto (assercao sobre log) | Gate de PR (com fake) + nightly (real) |
| **Eficacia / qualidade** | resposta correta, fundamentada, escala fora de escopo | Baixo (LLM nao-deterministico) | Eval em nightly/pre-release, nunca gate de PR |
| **Resiliencia** | SLA e fallback seguram sob falha | Total (fakes + clock injetavel) | Gate de PR |

A maior armadilha e colocar LLM real no caminho que bloqueia cada commit. **Regra de ouro:
o LLM real fica fora da suite deterministica.** A maioria dos testes usa fake da `LLMPort`.

## As tres camadas de "e2e"

**Camada 1 - Transporte/infra (deterministico, sem LLM real).**
Caminho completo da aplicacao (HTTP -> app -> caso de uso -> fallback -> adapter) com o
provider LLM *fake*. Roda a cada PR. Valida contrato, status codes, formato de saida,
ordem de fallback, timeout, backoff, degradacao graciosa. E o e2e que bloqueia o merge.

**Camada 2 - Comportamento do agente (LLM real, nao-deterministico).**
Testa se o agente de fato responde fundamentado, escala fora de escopo, nao alucina.
Usa LLM real ou Ollama local. Caro, lento, instavel: roda em pipeline separado
(nightly/pre-release), nunca bloqueando cada PR. E o **eval** (secao proria abaixo).

**Camada 3 - Sistema real (deploy live).**
Smoke test contra o API Gateway + Lambda publicado, ou contra o LocalStack. Valida
empacotamento, handler Mangum, env vars e permissoes. Roda em pre-release.

## Fakes: a fundacao de tudo

Como o caso de uso depende apenas de uma port (`LLMPort`), um fake simples cobre quase
todos os cenarios sem rede. Padronize estes tres fakes em `tests/fakes/`:

```python
# tests/fakes/llm.py
import time
from typing import Iterator, Sequence
from suporte.domain.ports import LLMPort, Answer
from suporte.domain.exceptions import ProviderUnavailable


class FakeLLM:
    """Retorna um Answer fixo. Base dos testes da camada 1."""
    def __init__(self, answer: Answer):
        self._answer = answer

    def ask(self, prompt: str) -> Answer:
        return self._answer

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield from self._answer.text.split()


class ProgrammableAdapter:
    """Adapter fake para exercitar fallback. Falha N vezes, depois responde
    (ou falha sempre). Registra quantas vezes foi chamado."""
    def __init__(self, name: str, *, fail_times: int = 0, delay_s: float = 0.0,
                 answer: Answer | None = None):
        self.name = name
        self._fail_times = fail_times
        self._delay_s = delay_s
        self._answer = answer
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        if self._delay_s:
            time.sleep(self._delay_s)          # troque por clock fake em teste de SLA
        if self.calls <= self._fail_times or self._answer is None:
            raise ProviderUnavailable([self.name])
        return self._answer

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield self.ask(prompt).text


class FakeClock:
    """Relogio controlavel para testar timeout/backoff sem sleep real."""
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:      # compativel com clock=time.monotonic
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
```

O `FallbackLLM` deve receber `clock` injetavel exatamente para isso. Se um componente usa
`time.sleep`/`time.monotonic` direto, refatore para receber o clock por parametro - senao
a suite fica lenta e intermitente.

## Camada 1 - e2e deterministico

### Caso de uso com fake

```python
# tests/application/test_answer_question.py
from suporte.application.use_cases.answer_question import AnswerQuestion
from suporte.application.dto import AnswerRequest, Mode

def test_pergunta_vazia_nao_chama_llm():
    llm = FakeLLM(answer=...)                 # nao deve ser chamado
    uc = AnswerQuestion(llm=llm)
    resp = uc.execute(AnswerRequest("   ", Mode.RUN))
    assert resp.escalated is False
    assert "invalida" in resp.text.lower()

def test_out_of_scope_escala_para_humano():
    llm = FakeLLM(answer=Answer(text="", provider="openai", prompt_tokens=5,
                                completion_tokens=0, latency_ms=12.0, out_of_scope=True))
    resp = AnswerQuestion(llm=llm).execute(AnswerRequest("qual a capital da Franca?"))
    assert resp.out_of_scope is True and resp.escalated is True
```

### FastAPI com TestClient

```python
# tests/interface/test_api.py
from fastapi.testclient import TestClient
from suporte.interface.api.api import app

client = TestClient(app)

def test_health():
    assert client.get("/health").status_code == 200

def test_validacao_rejeita_pergunta_vazia_com_422():
    assert client.post("/chat", json={"question": "", "mode": "run"}).status_code == 422

def test_pergunta_acima_de_4000_e_422():
    assert client.post("/chat", json={"question": "x" * 4001}).status_code == 422

def test_erro_de_dominio_vira_502_sem_stacktrace(monkeypatch):
    # injeta um caso de uso que levanta DomainError; resposta nao vaza detalhe interno
    r = client.post("/chat", json={"question": "ola"})
    if r.status_code == 502:
        assert "traceback" not in r.text.lower()
```

### Teste estatico de arquitetura

Garanta que a Agno nao vaza das camadas erradas (ver `agno-project-architecture-solid`):

```python
# tests/infrastructure/test_no_agno_in_api.py
import ast, pathlib

def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names

def test_camada_interface_nao_importa_agno():
    for py in pathlib.Path("src/suporte/interface").rglob("*.py"):
        assert "agno" not in _imports(py), f"{py} importa agno"

def test_dominio_e_aplicacao_nao_importam_agno_nem_sdk():
    for base in ("src/suporte/domain", "src/suporte/application"):
        for py in pathlib.Path(base).rglob("*.py"):
            imported = _imports(py)
            assert not (imported & {"agno", "openai", "groq"}), f"{py} vaza framework"
```

## Camada 1/3 - Resiliencia deterministica (o coracao do agente)

Os testes de maior valor e totalmente deterministicos. Prioritize-os.

```python
# tests/infrastructure/test_fallback_llm.py
from suporte.infrastructure.llm.fallback_llm import FallbackLLM
from suporte.domain.exceptions import ProviderUnavailable

def _fallback(providers, clock):
    return FallbackLLM(providers, per_attempt_timeout_s=10, total_timeout_s=25,
                       max_attempts_per_provider=2, backoff_base_s=0.5,
                       logger=RecordingLogger(), clock=clock)

def test_primario_responde_sem_fallback():
    clock = FakeClock()
    primary = ProgrammableAdapter("openai", answer=ok_answer("openai"))
    ans = _fallback([primary, ProgrammableAdapter("groq")], clock).ask("q")
    assert ans.provider == "openai"

def test_primario_falha_secundario_responde():
    clock = FakeClock()
    primary = ProgrammableAdapter("openai", fail_times=99)
    secondary = ProgrammableAdapter("groq", answer=ok_answer("groq"))
    ans = _fallback([primary, secondary], clock).ask("q")
    assert ans.provider == "groq"
    assert primary.calls == 2          # esgotou max_attempts_per_provider

def test_todos_falham_levanta_provider_unavailable_com_ordem():
    clock = FakeClock()
    a = ProgrammableAdapter("openai", fail_times=99)
    b = ProgrammableAdapter("groq", fail_times=99)
    try:
        _fallback([a, b], clock).ask("q")
        assert False, "deveria ter levantado"
    except ProviderUnavailable as exc:
        assert exc.attempts == ["openai", "groq"]

def test_fallback_respeita_sla_total():
    # eficiencia sob falha: primario estoura per_attempt; secundario responde
    clock = FakeClock()
    slow = ProgrammableAdapter("openai", fail_times=99)   # com clock: cada attempt avanca
    fast = ProgrammableAdapter("groq", answer=ok_answer("groq"))
    _fallback([slow, fast], clock).ask("q")
    assert clock.now <= 25             # nao excedeu total_timeout_s
```

Cubra tambem a validacao de config em `load_settings` (deve ser deterministica):
`total_timeout_s < per_attempt_timeout_s` -> `ValueError`; provider desconhecido ->
`ValueError` nomeando o provider; ordem vazia -> `ValueError`.

## Eficiencia operacional (assercao sobre o log estruturado)

Nao instrumente de novo: o `StructuredLogger` ja emite `latency_ms`, tokens,
`used_fallback`, `attempts`, `result` (ver `agno-observability`). Capture os logs, agregue
e asserte contra **orcamentos (budgets)**.

```python
# tests/eval/test_operational_efficiency.py
import statistics

def p95(values):
    values = sorted(values)
    k = max(0, int(round(0.95 * (len(values) - 1))))
    return values[k]

def test_latencia_p95_dentro_do_sla(captured_logs):
    assert p95([l["latency_ms"] for l in captured_logs]) <= 3000

def test_orcamento_de_tokens(captured_logs):
    avg = statistics.mean(l["prompt_tokens"] + l["completion_tokens"] for l in captured_logs)
    assert avg <= 800

def test_taxa_de_fallback(captured_logs):
    rate = sum(l["used_fallback"] for l in captured_logs) / len(captured_logs)
    assert rate <= 0.10

def test_nenhuma_requisicao_excede_total_timeout(captured_logs):
    assert all(l["latency_ms"] <= 25_000 for l in captured_logs)
```

Regras:
- Use **percentil (p95/p99)**, nunca media nem maximo. p95 reflete SLA; maximo e ruido.
- Para medir o overhead da SUA orquestracao (nao o LLM), use fake com `delay_s` conhecido.
  Com LLM real, meca ponta a ponta e aceite variancia.
- Budgets vem dos requisitos do proprio agente (ex.: `per_attempt_timeout_s`,
  `total_timeout_s`).

## Eval de eficacia (qualidade da resposta)

Aqui esta o sentido de "eficiencia" que a maioria quer: quao bem o agente acerta. Nao se
compara string exata - mede-se acerto sobre um **dataset de avaliacao** (golden set). Tres
pecas.

### a) Dataset versionado (`tests/eval/dataset.jsonl`)

```jsonl
{"id": "q1", "question": "Como reseto minha senha?", "expects": "in_scope", "must_mention": ["configuracoes", "e-mail"]}
{"id": "q2", "question": "Qual a capital da Franca?", "expects": "out_of_scope"}
{"id": "q3", "question": "Voces tem plano gratuito?", "expects": "in_scope", "must_mention": ["free tier"]}
```

Comece pequeno (20-50 casos): in-scope facil, in-scope dificil, out-of-scope que DEVE
escalar. Cresce conforme voce encontra falhas em producao. E o ativo mais importante do
eval.

### b) Scorers (do mais barato/confiavel ao mais caro)

```python
# tests/eval/scorers.py
def score_deterministic(case: dict, resp) -> bool:
    if case["expects"] == "out_of_scope":
        return resp.escalated is True                    # nao pode alucinar
    if not resp.text.strip():
        return False                                     # in_scope exige texto
    return all(m.lower() in resp.text.lower() for m in case.get("must_mention", []))

def score_grounded_llm_judge(case: dict, resp, judge_llm) -> float:
    """LLM-as-judge: 0..1 se a resposta esta fundamentada na doc. Caro e ruidoso;
    use como sinal, NUNCA como gate rigido por caso."""
    verdict = judge_llm.ask(
        f"Doc: {case.get('context','')}\nResposta: {resp.text}\n"
        "A resposta esta fundamentada na doc? Responda so 0 ou 1."
    )
    return 1.0 if verdict.text.strip().startswith("1") else 0.0
```

### c) Harness + agregacao com threshold

```python
# tests/eval/test_agent_quality.py  (marcado como e2e; roda em nightly/pre-release)
import json, pathlib, pytest
from suporte.composition_root import build_answer_question
from suporte.application.dto import AnswerRequest

def load_dataset():
    path = pathlib.Path(__file__).parent / "dataset.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

@pytest.mark.e2e
def test_acuracia_geral_acima_do_minimo():
    uc = build_answer_question()                 # LLM real (ou Ollama via MODEL_PROVIDER_ORDER)
    cases = load_dataset()
    passed = sum(score_deterministic(c, uc.execute(AnswerRequest(c["question"]))) for c in cases)
    accuracy = passed / len(cases)
    assert accuracy >= 0.90, f"acuracia {accuracy:.0%} abaixo de 90%"

@pytest.mark.e2e
def test_recall_de_out_of_scope():
    """A metrica mais critica: alucinar onde devia escalar e o pior erro."""
    uc = build_answer_question()
    oos = [c for c in load_dataset() if c["expects"] == "out_of_scope"]
    escalated = sum(uc.execute(AnswerRequest(c["question"])).escalated for c in oos)
    assert escalated / len(oos) >= 0.95
```

- O gate e sobre a **taxa**, nunca sobre casos individuais.
- `out_of_scope recall` merece threshold mais alto que a acuracia geral (Requisito de nao
  alucinacao).
- Rode o eval de CI com o **Ollama** (`OllamaAdapter`, custo zero, local) e reserve o
  provider real para pre-release. Trocar e so mudar `MODEL_PROVIDER_ORDER` - a `LLMPort` e
  a mesma.
- Registre cada execucao (CSV/JSON com data, modelo, acuracia, p95). O valor do eval e ver
  a **regressao** ao trocar modelo/prompt/`provider_order`, nao o pass/fail isolado.

## Observabilidade como saida testavel

O log e o trace sao saida que voce asserta (conecta com `agno-observability`):

```python
def test_um_log_por_requisicao_com_campos_obrigatorios(captured_logs):
    assert len(captured_logs) == 1
    campos = {"question_hash", "provider", "latency_ms", "prompt_tokens",
              "completion_tokens", "used_fallback", "attempts", "result"}
    assert campos <= set(captured_logs[0])

def test_nao_vaza_pii_nem_chave(captured_logs, api_key):
    blob = json.dumps(captured_logs)
    assert api_key not in blob                    # nunca a chave
    assert "senha do cliente" not in blob         # pergunta so como question_hash

def test_request_id_correlaciona_log_e_span(captured_logs, exported_spans):
    log_id = captured_logs[0]["request_id"]
    assert any(s.attributes.get("app.request_id") == log_id for s in exported_spans)
```

Use o `InMemorySpanExporter` do OTel SDK para tornar o trace deterministico no teste.

## Camada 3 - Sistema real (smoke)

```python
# tests/system/test_smoke.py  (marcado; roda em pre-release contra LocalStack ou AWS)
import os, pytest, httpx

@pytest.mark.system
def test_health_e_chat_no_deploy():
    base = os.environ["AGENT_BASE_URL"]           # API Gateway (LocalStack ou AWS)
    assert httpx.get(f"{base}/health").status_code == 200
    r = httpx.post(f"{base}/chat", json={"question": "ola", "mode": "run"})
    assert r.status_code in (200, 502)            # 200 ok, 502 se providers indisponiveis
```

Para o handler Lambda, teste a traducao evento->resposta invocando `handler` com um evento
API Gateway sintetico - sem AWS. Reuse o bootstrap idempotente do LocalStack no setup.

## Marcadores e pipeline de CI

Marque por velocidade/dependencia em `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "e2e: comportamento com LLM real/Ollama (lento, nao-deterministico)",
    "system: smoke contra deploy real (LocalStack/AWS)",
]
```

- **Gate de PR (rapido, deterministico):** `uv run pytest -m "not e2e and not system"`.
  Camada 1 + resiliencia + eficiencia operacional com fake + observabilidade. Bloqueia merge.
- **Nightly:** `uv run pytest -m e2e` com Ollama. Eval de eficacia + eficiencia real. Nao
  bloqueia PR; alerta em regressao.
- **Pre-release:** `-m e2e` com provider real + `-m system` contra LocalStack/AWS.

## Cuidados com o nao-determinismo (honestidade)

- Fixe `temperature` baixa (idealmente 0) no ambiente de eval para reduzir variancia.
- Thresholds com folga (90%, nao 100%): eval que falha por ruido vira alarme ignorado.
- Rode cada caso critico algumas vezes e use a maioria, se precisar de estabilidade.
- LLM-as-judge e sinal, nao verdade absoluta - valide o juiz contra alguns casos rotulados
  a mao antes de confiar nele.

## Estrutura de pastas de teste (padrao do workspace)

```
tests/
├── fakes/
│   └── llm.py                     # FakeLLM, ProgrammableAdapter, FakeClock
├── domain/
├── application/
│   └── test_answer_question.py    # caso de uso com fake (sem rede)
├── infrastructure/
│   ├── test_fallback_llm.py       # timeout/retry/fallback (clock + adapters fake)
│   ├── test_no_agno_in_api.py     # estatico: camadas nao vazam framework
│   └── test_observability.py      # log unico, sem PII, request_id correlacionado
├── interface/
│   └── test_api.py                # TestClient: 200/422/502, /health
├── eval/
│   ├── dataset.jsonl              # golden set versionado
│   ├── scorers.py                 # deterministico + LLM-as-judge
│   ├── test_agent_quality.py      # @pytest.mark.e2e (Ollama/real)
│   └── test_operational_efficiency.py  # p95, tokens, taxa de fallback
└── system/
    └── test_smoke.py              # @pytest.mark.system (deploy real)
```

## Ordem de implementacao (maior retorno primeiro)

1. `tests/fakes/llm.py` - a fundacao (FakeLLM, ProgrammableAdapter, FakeClock).
2. `test_fallback_llm.py` - resiliencia/SLA (deterministico, alto valor).
3. `test_answer_question.py` - validacao, out-of-scope, degradacao graciosa.
4. `test_api.py` - e2e deterministico de transporte (422/502/health).
5. Observabilidade (`request_id`, log unico, sem PII).
6. `test_operational_efficiency.py` - budgets de latencia/tokens/fallback.
7. So entao, num marcador separado, o eval de eficacia (Ollama) e o smoke (LocalStack).

## Checklist de testes (Definition of Done por projeto)

- [ ] `tests/fakes/` com FakeLLM, adapter programavel e clock fake.
- [ ] Suite deterministica (camada 1) cobrindo caso de uso, API e ordem de fallback.
- [ ] Resiliencia: primario-ok, fallback-ok, todos-falham, SLA total, config invalida.
- [ ] Eficiencia operacional: p95 de latencia, orcamento de tokens, taxa de fallback.
- [ ] Observabilidade: exatamente um log por requisicao, sem PII/chave, request_id no log E no span.
- [ ] Teste estatico de arquitetura (Agno so em infrastructure).
- [ ] Golden set versionado (`dataset.jsonl`) com casos in-scope e out-of-scope.
- [ ] Eval de eficacia com scorers (deterministico + judge) e threshold sobre a TAXA.
- [ ] `out_of_scope recall` com threshold proprio (mais alto que a acuracia geral).
- [ ] Marcadores `e2e`/`system`; gate de PR roda so o deterministico.
- [ ] Eval de CI com Ollama (custo zero); provider real so em pre-release.
- [ ] Smoke contra LocalStack/AWS antes do release.
- [ ] Resultados de eval registrados ao longo do tempo (deteccao de regressao).
- [ ] temperature baixa e thresholds com folga no ambiente de eval.
