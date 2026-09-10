---
name: agno-testing-strategy
description: Estrategia de testes automatizados, e2e, eval e OTIMIZACAO para agentes Agno v2 - as tres camadas de teste (transporte deterministico, comportamento do agente, sistema real), eficiencia operacional (latencia/tokens/fallback como assercao sobre o log estruturado), eval de eficacia com golden set e scorers (deterministico + LLM-as-judge), resiliencia deterministica (timeout/retry/fallback com clock e adapters fake), harness em pytest, marcadores e pipeline de CI, ALEM de busca e otimizacao automatica de parametros/modelos/prompt (Optuna para hiperparametros de sampling e RAG, DSPy para prompt/few-shot, promptfoo e Ragas para comparacao e eval de RAG, roteamento de modelos por custo/qualidade), com holdout, orcamento de trials e cache para evitar overfit e custo. Use ao escrever, organizar, revisar ou OTIMIZAR testes/qualidade de qualquer agente Agno do workspace (CLI, FastAPI, Lambda, AgentOS).
---

# Estrategia de testes para agentes Agno (unidade, e2e e eval)

Guia obrigatorio de testes para **todos os projetos de agente** deste workspace. Define o
que testar, como automatizar e como transformar cada teste num gate objetivo (pass/fail),
nao numa metrica solta. Serve para agentes entregues como CLI, funcao chamavel, FastAPI +
API Gateway e AgentOS.

Le junto de `agno-observability` (o log estruturado e a base das assercoes de eficiencia),
`agno-project-architecture-solid` (as camadas ports & adapters que tornam o agente
testavel) e `agno-agentos-eval-deploy` (inspecao manual de traces/sessions na UI, que este
guia automatiza). Para a parte de **otimizacao**, conecta com `agno-prompt-system` (a
instrucao e a variavel de maior impacto), `agno-providers` (todo parametro do modelo e
configuravel por env var - e exatamente isso que o otimizador varia), `agno-knowledge-rag`
(parametros de retrieval que tambem entram na busca) e `agno-secrets` (chaves resolvidas
por env/Secrets Manager que a funcao objetivo consome sem hardcode).

> **Como usar esta skill.** Ela tem duas metades. A primeira (ate "Eval de eficacia")
> ensina a **medir** o agente com gates objetivos. A segunda ("Otimizacao e busca de
> parametros, modelos e prompt") ensina a **melhorar** o agente automaticamente por cima
> dessa medicao. A regra e inviolavel: **so otimize o que voce ja sabe medir.** Sem golden
> set + scorers confiaveis, qualquer "otimizacao" e ruido caro.

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

## Otimizacao e busca de parametros, modelos e prompt

Ate aqui a skill mede o agente. Esta secao usa essa medicao como **funcao objetivo** para
melhorar o agente de forma sistematica, em vez de ajustar `temperature` no olho. A tese e
simples: **um agente tem parametros, e parametros se otimizam contra uma metrica.** Mas
otimizacao de LLM tem armadilhas proprias (custo por trial, nao-determinismo, overfit no
golden set) que a maioria dos tutoriais ignora. Trate esta secao como o guia para fazer
isso sem se enganar.

### O que da (e o que nao da) para otimizar

Separe os "parametros" por natureza - cada grupo pede uma ferramenta diferente. Misturar
tudo num otimizador so e o erro classico.

| Grupo | Exemplos | Ferramenta indicada | Custo por trial |
|-------|----------|---------------------|-----------------|
| **Sampling** | `temperature`, `top_p`, `top_k`, `max_tokens` | Optuna (TPE) | N chamadas LLM |
| **RAG / retrieval** | `top_k`, chunk size, overlap, threshold de similaridade, reranker on/off | Optuna + Ragas | N chamadas + indexacao |
| **Prompt / instrucao / few-shot** | texto do system prompt, exemplos, formato de saida | DSPy (MIPROv2/GEPA) ou promptfoo | alto (busca sobre candidatos) |
| **Modelo / roteamento** | provider primario, ordem de fallback, modelo por faixa de custo | promptfoo (matriz) + roteador | N x M (modelos x casos) |
| **Orquestracao** | `per_attempt_timeout_s`, `total_timeout_s`, `max_attempts`, `backoff_base_s` | Optuna (multi-objetivo) | N chamadas |

Regra de escopo: **prompt e o de maior impacto, sampling e o mais barato de varrer, modelo
e o de maior alavanca de custo.** Se so puder atacar um, comece pelo prompt.

### Pre-requisito absoluto: a funcao objetivo

Todo otimizador precisa de uma funcao `params -> score` estavel. Ela reaproveita o golden
set e os scorers ja definidos. Um ponto de arquitetura que este workspace ja facilita:
**cada parametro do agente e configurado por env var** (ver `agno-providers`,
`agno-secrets`). Entao o otimizador nao precisa conhecer o codigo do agente - ele so injeta
valores nas envs, reconstroi o agente pelo `composition_root` e mede.

```python
# tests/optimization/objective.py
import os, statistics
from suporte.composition_root import build_answer_question
from suporte.application.dto import AnswerRequest
from tests.eval.scorers import score_deterministic

def evaluate(params: dict, cases: list[dict]) -> dict:
    """Contrato unico: aplica params por env var, roda o golden set, devolve metricas.
    Nunca faz hardcode de segredo; so mexe em parametros de comportamento."""
    for key, value in params.items():
        os.environ[key] = str(value)          # ex.: MODEL_TEMPERATURE, RAG_TOP_K

    uc = build_answer_question()              # reconstroi com os params atuais
    latencies, passed = [], 0
    for case in cases:
        resp = uc.execute(AnswerRequest(case["question"]))
        passed += int(score_deterministic(case, resp))
        latencies.append(resp.latency_ms)

    return {
        "accuracy": passed / len(cases),
        "p95_latency_ms": _p95(latencies),
        "avg_tokens": statistics.mean(...),   # do log estruturado
    }
```

Essa funcao e a fronteira. Optuna, DSPy e promptfoo sao apenas motores diferentes que a
chamam. Se `evaluate` estiver instavel (varia muito entre execucoes iguais), **pare e
conserte antes de otimizar** - senao voce otimiza ruido.

### Overfit e honestidade: holdout obrigatorio

O erro numero um em otimizacao de LLM: rodar 200 trials no golden set inteiro, escolher o
melhor e comemorar. Voce so aprendeu a decorar aquele conjunto. Separe **sempre** tres
particoes, como em ML classico:

- **train/dev** (~60%): o otimizador ve e otimiza contra este.
- **validation** (~20%): usado para escolher entre os melhores candidatos e detectar
  overfit (se train sobe e validation cai, parou de generalizar).
- **holdout/test** (~20%): **o otimizador NUNCA ve.** So e tocado uma vez, no fim, para
  reportar o numero honesto. Se voce olhou o holdout e voltou a otimizar, ele virou
  validation e voce perdeu a medida honesta.

Fixe a particao por hash do `id` do caso (deterministico, reproduzivel entre execucoes),
nao por `random.shuffle` sem seed.

### Optuna - busca de sampling, RAG e orquestracao

[Optuna](https://optuna.readthedocs.io/en/stable/) e o padrao em Python para busca
"define-by-run": voce escreve uma funcao `objective(trial)` que pede cada parametro ao
`trial` e devolve o score. O sampler default (TPE, Tree-structured Parzen Estimator)
aprende com os trials anteriores e concentra a busca nas regioes promissoras - muito melhor
que grid/random para orcamento pequeno. Conteudo tecnico condensado das
[docs oficiais do Optuna](https://optuna.readthedocs.io/) e rephrased para conformidade.

```python
# tests/optimization/optimize_sampling.py
import optuna
from tests.optimization.objective import evaluate
from tests.optimization.dataset import load_split   # train/validation/holdout por hash

def objective(trial: optuna.Trial) -> float:
    params = {
        "MODEL_TEMPERATURE": trial.suggest_float("temperature", 0.0, 1.0),
        "MODEL_TOP_P":       trial.suggest_float("top_p", 0.5, 1.0),
        "MODEL_MAX_TOKENS":  trial.suggest_int("max_tokens", 256, 2048, step=128),
        "RAG_TOP_K":         trial.suggest_int("rag_top_k", 2, 8),
    }
    metrics = evaluate(params, load_split("train"))

    # Pruning: aborta trials ruins cedo para nao gastar chamadas de LLM.
    trial.report(metrics["accuracy"], step=0)
    if trial.should_prune():
        raise optuna.TrialPruned()

    return metrics["accuracy"]

def main() -> None:
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),          # reproduzivel
        pruner=optuna.pruners.MedianPruner(),
        storage="sqlite:///tests/optimization/study.db",       # retomavel e auditavel
        study_name="sampling-v1",
        load_if_exists=True,
    )
    study.optimize(objective, n_trials=40, timeout=3600)       # ORCAMENTO explicito

    print("melhor score (train):", study.best_value)
    print("melhores params:", study.best_params)
    # valida no holdout UMA vez, com os melhores params:
    print("holdout:", evaluate(study.best_params, load_split("holdout")))
```

Boas praticas de Optuna para agentes LLM:

- **Sempre defina orcamento** (`n_trials` E `timeout`). Cada trial dispara N chamadas de
  LLM; sem teto, a conta explode. Comece com 20-40 trials.
- **Ative pruning** (`MedianPruner`, `HyperbandPruner`) para matar trials ruins cedo.
- **Use `storage` sqlite**: o study fica retomavel (roda em pedacos) e auditavel depois.
- **Fixe `seed` no sampler** para reproducibilidade - mas lembre que o LLM ainda tem
  variancia propria; rode a funcao objetivo com `temperature` baixa no eval.
- **Multi-objetivo quando ha trade-off** (qualidade x latencia x custo): use
  `directions=["maximize", "minimize"]` e analise a **fronteira de Pareto**, nao um numero
  so. Nao existe "melhor" absoluto quando custo importa.

```python
# multi-objetivo: qualidade vs custo (fronteira de Pareto)
def objective_multi(trial):
    params = {...}
    m = evaluate(params, load_split("train"))
    return m["accuracy"], m["avg_tokens"]        # maximizar acuracia, minimizar tokens

study = optuna.create_study(directions=["maximize", "minimize"],
                            sampler=optuna.samplers.NSGAIISampler(seed=42))
```

### Cache: o que torna a busca viavel

Sem cache, 40 trials x 50 casos = 2000 chamadas de LLM por rodada. Duas defesas:

1. **Cache de respostas do LLM na busca**: chaveie por `(prompt, params_de_sampling)`. Se
   o par ja foi visto, reutilize. Isso corta drasticamente o custo de re-execucoes.
2. **Ollama local durante a busca** (`agno-local-dev-environment`): custo zero, itere a
   vontade; confirme o vencedor no provider real so no fim. Trocar e mudar
   `MODEL_PROVIDER_ORDER` - a `LLMPort` e a mesma.

Cuidado: cache mascara a **latencia real**. Se o objetivo inclui p95, meca latencia sem
cache (ou com um fake de `delay_s` conhecido) para nao otimizar contra um numero irreal.

### DSPy - otimizacao automatica de prompt e few-shot

Quando a variavel e o **texto do prompt**, o motor certo e o [DSPy](https://dspy.ai/): ele
trata a instrucao e os exemplos few-shot como parametros e "compila" o programa contra uma
metrica, propondo e testando candidatos automaticamente. Escolha do otimizador (resumido da
[documentacao do DSPy](https://dspy.ai/diving-deeper/choosing-an-optimizer/), rephrased para
conformidade):

- **`BootstrapFewShot`**: gera exemplos few-shot a partir do seu dataset. Barato, bom ponto
  de partida com poucos dados.
- **`MIPROv2`**: otimiza instrucao E few-shot em conjunto via busca bayesiana. Mais caro,
  ganha em tarefas complexas.
- **`GEPA`**: otimizador reflexivo (le o feedback textual da metrica e propoe melhorias
  mantendo uma fronteira de Pareto); recomendado para workloads complexos.

```python
# tests/optimization/optimize_prompt.py
import dspy

dspy.configure(lm=dspy.LM("ollama/llama3", api_base="http://localhost:11434"))

class Suporte(dspy.Signature):
    """Responde duvidas de suporte fundamentado na doc; escala fora de escopo."""
    question: str = dspy.InputField()
    answer: str = dspy.OutputField()
    escalate: bool = dspy.OutputField()

program = dspy.Predict(Suporte)

def metric(example, pred, trace=None) -> float:
    # reusa a mesma logica de scorer do eval (deterministico + judge como sinal)
    return 1.0 if _matches(example, pred) else 0.0

optimizer = dspy.MIPROv2(metric=metric, auto="light")   # "light" = orcamento menor
compiled = optimizer.compile(program, trainset=load_split("train"),
                             valset=load_split("validation"))
compiled.save("tests/optimization/compiled_prompt.json")
```

O prompt otimizado que o DSPy produz e um **artefato versionavel**. No workspace, a fonte de
verdade do prompt fica em bucket S3 com fallback local (`agno-prompt-system`): promova o
prompt compilado para la depois de validado no holdout, com a metrica que justificou a
troca registrada.

### promptfoo - comparacao de modelos e prompts em matriz

Para **decidir entre modelos** ou variacoes de prompt de forma visual e versionada,
[promptfoo](https://www.promptfoo.dev/docs/configuration/guide) roda cada prompt contra cada
caso e monta uma matriz de comparacao (rephrased da doc oficial para conformidade). Ideal na
fase de escolha, e roda em CI.

```yaml
# tests/optimization/promptfooconfig.yaml
providers:
  - openai:gpt-4o-mini
  - groq:llama-3.3-70b-versatile
  - ollama:chat:llama3
prompts:
  - file://prompts/v1.txt
  - file://prompts/v2.txt
tests:
  - vars: { question: "Como reseto minha senha?" }
    assert:
      - type: icontains
        value: "configuracoes"
      - type: llm-rubric              # LLM-as-judge com threshold (default 0.7)
        value: "Responde fundamentado e nao inventa politica inexistente"
```

Rode com `npx promptfoo eval` e compare custo/latencia/qualidade lado a lado. Trate o
`llm-rubric` como sinal (LLM-as-judge), nao verdade absoluta - vale a mesma ressalva do
eval de eficacia.

### Ragas - eval especifico de RAG

Se o agente usa RAG (`agno-knowledge-rag`), a qualidade nao e so "resposta certa": e
**faithfulness** (a resposta se sustenta no contexto recuperado?), **context precision** e
**context recall**. [Ragas](https://docs.ragas.io/) mede essas dimensoes e ate integra com
o otimizador do DSPy para melhorar os prompts das metricas. Use Ragas para decidir os
parametros de retrieval (`RAG_TOP_K`, chunk size, reranker) que voce varre no Optuna - a
metrica de faithfulness vira o `score` da funcao objetivo.

### Fluxo recomendado (do barato ao caro)

```
1. Eval confiavel  ──►  golden set + scorers + holdout separado   (pre-requisito)
2. Sampling        ──►  Optuna (TPE, pruning, Ollama, cache)       (barato, rapido)
3. RAG             ──►  Optuna + Ragas (faithfulness/recall)       (medio)
4. Prompt          ──►  DSPy (BootstrapFewShot -> MIPROv2/GEPA)    (alto impacto)
5. Modelo          ──►  promptfoo (matriz custo x qualidade)       (decisao de arquitetura)
6. Validar no HOLDOUT uma vez  ──►  promover artefato + registrar a metrica que justificou
```

Cada etapa so avanca se a anterior estabilizou. E cada promocao (prompt novo, modelo novo,
`provider_order` novo) entra como uma linha no historico de eval - assim voce mede a
**regressao** na proxima mudanca, nao so o ganho pontual.

### Anti-padroes de otimizacao (o que NAO fazer)

- **Otimizar sem holdout.** Voce so vai decorar o golden set e o ganho nao generaliza.
- **Sem orcamento de trials.** A conta de tokens explode silenciosamente.
- **Otimizar contra o LLM-as-judge como verdade.** O juiz tem vies; valide-o contra casos
  rotulados a mao antes de confiar. Prefira scorer deterministico como metrica principal.
- **Cache mascarando latencia.** Nao otimize p95 com respostas cacheadas.
- **Mudar varios grupos de uma vez** (prompt + modelo + sampling juntos). Voce nao sabe o
  que causou o ganho/regressao. Otimize um grupo, fixe, passe ao proximo.
- **Confundir eval de PR com otimizacao.** Otimizacao e cara e roda offline/nightly, NUNCA
  no gate que bloqueia commit.

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
    "optimization: busca/otimizacao de parametros/modelo/prompt (caro, offline)",
]
```

- **Gate de PR (rapido, deterministico):** `uv run pytest -m "not e2e and not system and not optimization"`.
  Camada 1 + resiliencia + eficiencia operacional com fake + observabilidade. Bloqueia merge.
- **Nightly:** `uv run pytest -m e2e` com Ollama. Eval de eficacia + eficiencia real. Nao
  bloqueia PR; alerta em regressao.
- **Pre-release:** `-m e2e` com provider real + `-m system` contra LocalStack/AWS.
- **Otimizacao (sob demanda, offline):** scripts em `tests/optimization/` (Optuna, DSPy,
  promptfoo). NUNCA no gate de PR - sao caros e nao-deterministicos. Rode manualmente ou em
  um workflow dedicado; o resultado e um artefato promovido (prompt/params/modelo) mais uma
  linha no historico de eval, nao um pass/fail.

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
├── optimization/                  # busca/otimizacao (offline, caro; nao roda no PR)
│   ├── dataset.py                 # split train/validation/holdout por hash do id
│   ├── objective.py               # funcao params -> score (reusa scorers do eval)
│   ├── optimize_sampling.py       # Optuna: temperature/top_p/max_tokens/RAG_TOP_K
│   ├── optimize_prompt.py         # DSPy: instrucao + few-shot (MIPROv2/GEPA)
│   ├── promptfooconfig.yaml       # matriz de modelos x prompts
│   └── study.db                   # storage retomavel/auditavel do Optuna
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
8. **Por ultimo, e so quando o eval estiver estavel**, a otimizacao (`tests/optimization/`):
   funcao objetivo + holdout, depois Optuna (sampling/RAG), DSPy (prompt) e promptfoo
   (modelo). Otimizar antes de medir bem e desperdicio de tokens.

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

### Otimizacao (quando for buscar parametros/modelo/prompt)

- [ ] Funcao objetivo unica (`params -> score`) reusando os scorers do eval, injetando
      params por env var (sem hardcode de segredo).
- [ ] Split train/validation/**holdout** por hash do `id`; holdout tocado uma unica vez no fim.
- [ ] Optuna com **orcamento** (`n_trials` + `timeout`), pruning e `storage` sqlite retomavel.
- [ ] Multi-objetivo (Pareto) quando houver trade-off qualidade x latencia x custo.
- [ ] Cache de LLM e/ou Ollama local na busca; latencia medida SEM cache.
- [ ] DSPy para prompt/few-shot; prompt otimizado promovido como artefato versionado (S3/local).
- [ ] promptfoo para decidir modelo (matriz custo x qualidade); Ragas para parametros de RAG.
- [ ] Otimizacao roda offline/sob demanda (marcador `optimization`), NUNCA no gate de PR.
- [ ] Um so grupo de parametros por rodada; ganho validado no holdout e registrado no historico.
