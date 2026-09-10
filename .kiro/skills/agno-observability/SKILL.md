---
name: agno-observability
description: Boas praticas de observabilidade para agentes Agno v2 - tracing OpenTelemetry, log estruturado (configure_agno_logging), metricas e debug. Cobre stack local (SigNoz, Jaeger, Langfuse, Grafana) e AWS (CloudWatch Logs Insights, X-Ray via ADOT, metricas EMF, Lambda Powertools). Use ao instrumentar, depurar ou monitorar qualquer agente Agno, especialmente em Lambda/AgentOS.
---

# Observabilidade em agentes Agno (tracing, logs e metricas)

Guia para observar agentes Agno v2 em desenvolvimento e producao. Serve tanto para o
estudo local (CLI/AgentOS) quanto para deploy serverless (ver `agno-lambda-apigateway`).

Observabilidade responde tres perguntas: **o que o agente fez** (tracing), **o que a
sua aplicacao fez em volta** (logs/metricas) e **a infra esta saudavel** (telemetria da
plataforma). Cada uma pede um mecanismo diferente; nao tente cobrir tudo com uma so.

## Os tres sinais (e o que observar em cada um)

| Sinal | O que captura em um agente Agno | Mecanismo |
|-------|----------------------------------|-----------|
| **Traces** | Chamadas ao modelo, tool calls, delegacao em teams, steps de workflow, tokens, latencia por span | OpenTelemetry (auto-instrumentado pelo Agno) |
| **Logs** | Eventos da sua aplicacao: fallback, retry, SLA estourado, degradacao graciosa, resultado por requisicao | `logging` estruturado + `configure_agno_logging` |
| **Metricas** | Contadores e histogramas agregados: taxa de fallback, p95 de latencia, tokens/custo, erros por provider | Metricas OTel ou CloudWatch EMF |

Regra pratica: **traces** para investigar UMA execucao; **logs** para reconstruir o que
aconteceu; **metricas** para alarmar e ver tendencia. Nao gere metrica com dimensao de
alta cardinalidade (`session_id`, `user_id`) - isso explode custo. Esses campos vao no
log/trace, nunca como dimensao de metrica.

## O que o Agno oferece de fabrica

O Agno v2 e instrumentado com **OpenTelemetry** (padrao de tracing distribuido). Isso e
o que permite exportar para um backend local em dev e para a AWS em producao **sem mudar
o codigo do agente** - so muda a variavel de ambiente do exporter.

1. **Tracing** - cada run vira uma arvore de spans (modelo, tool, team, workflow) com
   input/output, status, duracao e tokens. Em AgentOS basta `tracing=True`. Fora do
   AgentOS (ex.: Lambda), voce inicializa a instrumentacao OTel manualmente.
2. **Custom logging** - `configure_agno_logging()` troca o logger padrao do Agno pelo
   seu, com o formato que voce quiser (ex.: JSON de uma linha). O Agno reconhece os
   loggers nomeados `agno` (agente), `agno-team` e `agno-workflow`.
3. **Debug mode** - `debug_mode=True` (ou `AGNO_DEBUG=true`) imprime mensagens enviadas
   ao modelo, tool calls e metricas. Otimo em dev; **desligado em producao** (verboso e
   pode vazar prompt/PII).

## Debug mode (desenvolvimento)

Para entender o fluxo de execucao passo a passo enquanto desenvolve:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat

agent = Agent(model=OpenAIChat(id="gpt-4o-mini"), debug_mode=True)
agent.print_response("Explique tracing em uma frase")
# imprime: mensagens ao modelo, tool calls, metricas de tokens/latencia
```

- Global: `export AGNO_DEBUG=true` liga o debug de todos os agentes.
- Por run: passe `debug_mode=True` so na chamada que quer inspecionar.
- Nao deixe ligado em producao: e verboso e loga conteudo sensivel.

## Log estruturado (o que substitui o logger do Agno)

Emita **JSON de uma linha** por evento - assim qualquer backend (CloudWatch, Loki)
parseia como campos e voce consulta por `provider`, `latency_ms`, `used_fallback`, etc.

```python
# infrastructure/observability/logging.py
import json
import logging

def build_json_logger(name: str = "agno") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()          # remove o handler default do Agno
    handler = logging.StreamHandler() # stdout -> CloudWatch em Lambda
    handler.setFormatter(logging.Formatter("%(message)s"))  # a msg ja e JSON
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

def log_request(logger: logging.Logger, *, request_id: str, provider: str,
                latency_ms: float, prompt_tokens: int, completion_tokens: int,
                used_fallback: bool, attempts: list[str], result: str) -> None:
    logger.info(json.dumps({
        "event": "llm_request",
        "request_id": request_id,        # correlaciona com o trace (ver abaixo)
        "provider": provider,
        "latency_ms": round(latency_ms, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "used_fallback": used_fallback,
        "attempts": attempts,
        "result": result,               # "ok" | "error"
    }, ensure_ascii=False))
```

Para os logs internos do Agno cairem no mesmo formato/destino:

```python
from agno.utils.log import configure_agno_logging

json_logger = build_json_logger("agno")
configure_agno_logging(
    custom_default_logger=json_logger,
    custom_agent_logger=json_logger,
)
# tracebacks em erros ficam desligados por padrao; ligue em dev se precisar:
# configure_agno_logging(enable_log_tracebacks=True)
```

Regras:
- Uma linha por evento (JSON compacto), nunca multilinha - facilita parse em log agg.
- Registre um evento por requisicao (sucesso e falha), sempre com `request_id`.
- **Nunca** logue chave de API, prompt cru ou resposta com PII. Prefira `question_hash`.

## Correlacionar log e trace (request_id)

Gere um `request_id` no inicio do run e coloque **no log e no span OTel**. Assim um
fallback logado por voce aponta para o span exato da chamada que falhou.

```python
import uuid
from opentelemetry import trace

def new_request_id() -> str:
    return uuid.uuid4().hex

# no inicio do handler / caso de uso:
request_id = new_request_id()
span = trace.get_current_span()
span.set_attribute("app.request_id", request_id)   # mesmo id no log e no trace
```

## Encaixe na arquitetura (ports & adapters)

Observabilidade e preocupacao de **infraestrutura** - o dominio nao a conhece (ver
`agno-project-architecture-solid`). Trate como duas camadas complementares:

- **Camada da sua aplicacao** - o log estruturado por requisicao (fallback, SLA, retry).
  Isso e insubstituivel: nenhum tracing pronto conhece a sua logica de negocio.
- **Camada do framework** - tracing OTel + `configure_agno_logging`. Configure ambos no
  `composition_root` (unico lugar que conhece o concreto), nunca no dominio nem no caso
  de uso.

Promova os campos do seu log a **metricas** quando quiser alarmar: contador de fallback,
histograma de `latency_ms` por provider, soma de tokens/custo.

## Regra: onde cada backend roda (dev x producao)

Decisao arquitetural fixa deste projeto:

- **Producao = CloudWatch + X-Ray (AWS), obrigatoriamente.** Logs em CloudWatch
  Logs, traces em X-Ray (via ADOT), metricas em CloudWatch (EMF) e alarmes em
  CloudWatch Alarms. Este e o caminho produtivo suportado — nao ha alternativa.
- **SigNoz / Grafana+Tempo+Loki+Prometheus / Langfuse sao SO de desenvolvimento
  local.** Servem para inspecionar/estudar em Docker e **nunca** vao para
  producao. Nao aponte um agente produtivo para esses backends.

O que **nao** muda entre os dois: o codigo do agente. O Agno exporta OTel e emite
log estruturado; trocar de destino (collector local -> X-Ray/CloudWatch) e so
mudar a configuracao/variavel de ambiente do exporter. A escolha do backend e de
infraestrutura/deploy, nao de codigo.

## Stack local (SOMENTE desenvolvimento)

Como o Agno fala OTel, aponte o exporter para um coletor local em Docker. Uso
restrito a dev/estudo — nada aqui e destino de producao (ver regra acima). Escolha
por objetivo:

- **SigNoz** ou **Jaeger + OpenTelemetry Collector** - recebem spans OTLP; SigNoz cobre
  traces + tokens num painel so.
- **Langfuse / Arize Phoenix / OpenLIT** - focadas em LLM; capturam custo, latencia e
  input/output por span. Boas para inspecionar prompt/resposta durante o estudo.
- **Grafana Tempo (traces) + Loki (logs) + Prometheus (metricas)** - os tres sinais com
  uma cara so. Loki casa bem com o log JSON acima.

Config por variavel de ambiente (o codigo do agente nao muda entre dev e prod):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=suporte-sla-fallback
```

## AWS (producao — CloudWatch + X-Ray, obrigatorio)

Em producao a observabilidade e feita **exclusivamente** com servicos gerenciados
da AWS: **CloudWatch** (logs e metricas) e **X-Ray** (traces). Em Lambda, casa com
o padrao de `agno-lambda-apigateway` (SAM + API Gateway). Mapeamento dos tres
sinais:

### Logs -> CloudWatch Logs

- Em Lambda, `stdout`/`logging` ja vai para o CloudWatch. Emita **JSON de uma linha**
  para virar campos consultaveis.
- Consulte com **CloudWatch Logs Insights** (taxa de fallback, p95 por provider):

```
fields @timestamp, provider, latency_ms, used_fallback
| filter event = "llm_request"
| stats count() as total, avg(latency_ms) as avg_ms,
        sum(used_fallback) as fallbacks by provider
```

### Traces -> X-Ray via ADOT

X-Ray e SEMPRE o backend de traces em producao (apos o deploy). Mas atencao a uma
distincao que muda o que voce precisa configurar — sao **dois niveis de trace**:

1. **Trace de infraestrutura (o que `Tracing: Active` da sozinho).** Segmentos do
   X-Ray para o **Lambda e o API Gateway**: cold start, duracao da invocacao,
   erros. Isso NAO conhece o miolo do agente — nao sabe "chamou o OpenAI", "fez
   fallback pro Groq" nem "buscou 5 chunks no RAG". E o contorno, nao o conteudo.
2. **Trace do agente Agno (precisa ser ligado explicitamente).** O Agno e
   instrumentado com **OpenTelemetry** e emite spans de modelo, tool, team,
   workflow, tokens e latencia por span. Esses spans sao **OTLP**, e o X-Ray NAO
   fala OTLP nativamente.

> **Regra pratica:** `Tracing: Active` **sozinho NAO cobre o trace do agente
> Agno** — cobre so Lambda + API Gateway. Para os spans internos do Agno chegarem
> ao X-Ray e obrigatoria a **ADOT layer** (AWS Distro for OpenTelemetry) rodando
> como extension no Lambda: ela recebe o OTLP do Agno e encaminha para o X-Ray.
> Sem a ADOT, voce ve so o segmento do Lambda.

Os **tres requisitos** para o X-Ray cobrir o trace do agente (todos necessarios):

1. **Anexar a ADOT layer** (ARN correto por regiao E arquitetura arm64/x86).
2. **`AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument`** e
   **`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`** (o collector da propria
   ADOT layer, no localhost do ambiente Lambda).
3. **`AWSXRayDaemonWriteAccess`** na funcao (permissao de envio ao X-Ray).

- Ative tambem `Tracing: Active` na funcao e `TracingEnabled: true` no API Gateway
  (nivel 1), para correlacionar o contorno AWS com os spans do Agno (nivel 2).
- Alternativa gerenciada mais nova: **CloudWatch Application Signals** (APM sobre OTel).
- **Cuidado com Lambda:** o export por batch nao combina com o congelamento do container.
  Use o ADOT Collector como extension (os requisitos acima ja fazem isso) ou force o
  flush dos spans ao fim do handler, senao voce perde traces.

> **Fidelidade (honestidade):** o modelo de dados do X-Ray (segments/subsegments) e
> mais pobre que o de um backend OTel dedicado. Atributos ricos de LLM (tokens,
> custo, prompt/resposta por span) viram metadata/annotations e nem sempre ficam tao
> navegaveis quanto no Langfuse/SigNoz (que sao SO de dev). Para producao unificada
> com o resto da AWS, X-Ray atende; para inspecao profunda de LLM, e mais fraco.

Trecho de `template.yaml` (SAM) — ADOT modular por ambiente (o ARN vem de
parametro, pois muda por regiao/arquitetura; ver `samconfig.toml`):

```yaml
Parameters:
  AdotLayerArn:
    Type: String
    Default: ""     # vazio = ADOT desligada (so trace de infra); preencha por ambiente
Conditions:
  UseAdot: !Not [!Equals [!Ref AdotLayerArn, ""]]
Globals:
  Function:
    Tracing: Active                      # nivel 1: X-Ray na funcao (Lambda)
Resources:
  ChatAgentFunction:
    Type: AWS::Serverless::Function
    Properties:
      Layers:
        - !Ref DependenciasLayer
        # nivel 2: ADOT so quando AdotLayerArn foi informado (por ambiente)
        - !If [UseAdot, !Ref AdotLayerArn, !Ref "AWS::NoValue"]
      Environment:
        Variables:
          AWS_LAMBDA_EXEC_WRAPPER: !If [UseAdot, "/opt/otel-instrument", !Ref "AWS::NoValue"]
          OTEL_EXPORTER_OTLP_ENDPOINT: !If [UseAdot, "http://localhost:4317", ""]
          OTEL_SERVICE_NAME: suporte-sla-fallback
      Policies:
        - AWSXRayDaemonWriteAccess       # permissao para enviar ao X-Ray
```

> Confirme o ARN/versao da ADOT layer para a sua regiao e arquitetura (arm64/x86) na
> documentacao oficial; ARNs mudam por regiao e versao. Defina-o por ambiente em
> `parameter_overrides` (dev/staging/prod), nunca fixo no template.

### Metricas -> CloudWatch (EMF)

- Emita metricas customizadas via **Embedded Metric Format**: voce loga um JSON e o
  CloudWatch extrai a metrica, sem chamada extra de API.
- Metricas uteis: contagem de fallback, tokens consumidos, custo estimado, out-of-scope,
  latencia por provider.

### Powertools + alarmes

- **aws-lambda-powertools (Python)** integra Logger (JSON), Tracer (X-Ray) e Metrics
  (EMF) num pacote so - a escolha natural para o handler Lambda.
- **CloudWatch Alarms** em cima das metricas: alarme quando a taxa de fallback sobe,
  quando o p95 estoura o SLA (ex.: `total_timeout_s`), ou quando erros de provider
  disparam. E onde a observabilidade vira acao.

## Observabilidade de RAG (retrieval)

Quando o agente tem `knowledge` (ver `agno-knowledge-rag`), o **passo de retrieval**
vira um alvo próprio de observabilidade — separado da chamada ao modelo. Duas
perguntas: **o retrieval trouxe o certo?** (eficácia) e **quanto pesou?** (eficiência).

Como tudo mais nesta skill, ligue/desligue por **variável de ambiente**, sem tocar no
código do domínio (a instrumentação vive em `infrastructure`/`composition_root`):

| Env var | Efeito |
|---------|--------|
| `RAG_TRACING_ENABLED` | liga os spans de retrieval (chunks recuperados, scores, latência da busca) |
| `RAG_METRICS_ENABLED` | liga métricas de retrieval (hit rate, chunks/consulta, tokens de contexto) |
| `RAG_TRACE_CONTENT` | inclui o **texto** dos chunks no span — **só em dev**; em prod vaza dados |
| `RAG_SAMPLE_RATE` | amostragem dos spans de retrieval em prod (ex.: 0.1) |

O que observar em cada sinal (específico de RAG):

- **Traces** — um span por busca no vector DB: `rag.vector_db` (lancedb/pgvector/
  s3vectors), `rag.search_type` (vector/keyword/hybrid), `rag.top_k`,
  `rag.chunks_returned`, `rag.top_score`, `rag.latency_ms`. Correlacione com o
  `request_id` (mesmo padrão de log↔trace abaixo).
- **Logs** — um evento por consulta ao conhecimento: quantos chunks entraram no
  contexto, tokens de contexto e se a resposta usou a base. Nunca logue o conteúdo
  bruto do chunk em prod (pode conter PII); logue `doc_id`/`chunk_id` e um hash.
- **Métricas** — `rag_hit_rate` (contra um golden set, ver `agno-testing-strategy`),
  histograma de `rag_latency_ms` por `vector_db`, soma de `context_tokens`. Não use
  `chunk_id`/`doc_id` como dimensão de métrica (alta cardinalidade) — vão no log/trace.

```python
# infrastructure/observability/tracing.py — span de retrieval (esboço)
from opentelemetry import trace

def trace_retrieval(*, vector_db: str, search_type: str, top_k: int,
                    chunks_returned: int, top_score: float, latency_ms: float,
                    include_content: bool = False, chunks: list[str] | None = None) -> None:
    span = trace.get_current_span()
    span.set_attribute("rag.vector_db", vector_db)
    span.set_attribute("rag.search_type", search_type)
    span.set_attribute("rag.top_k", top_k)
    span.set_attribute("rag.chunks_returned", chunks_returned)
    span.set_attribute("rag.top_score", round(top_score, 4))
    span.set_attribute("rag.latency_ms", round(latency_ms, 1))
    # RAG_TRACE_CONTENT só em dev: conteúdo do chunk pode conter PII.
    if include_content and chunks:
        span.set_attribute("rag.chunks_preview", " | ".join(c[:120] for c in chunks))
```

Onde visualizar localmente (ver `agno-local-dev-environment`): **AgentOS** mostra a
tool call de busca por run; **Langfuse** é o melhor para tokens de contexto e conteúdo
recuperado por span; **SigNoz/Grafana** para latência agregada do retrieval. Para
eficácia (recall@k), rode o golden set com **Ragas/DeepEval** (ver
`agno-testing-strategy`).

## Cuidados transversais

- **PII e segredos.** O tracing automatico captura input/output do modelo por padrao.
  Em producao, **redija ou omita** prompt/completion, ou logue so o `question_hash`.
  Nunca logue chave de API.
- **Custo.** Tracing 100% em producao fica caro (cobranca por span e por GB de log). Use
  **sampling** (ex.: 100% em erro, 5-10% em sucesso).
- **Cardinalidade.** `session_id`/`user_id` no log/trace, nunca como dimensao de metrica.
- **Ruido.** Desligue `debug_mode` em producao; use nivel INFO para eventos de negocio e
  WARNING/ERROR para falhas.

## Progressao sugerida (workspace de estudo)

1. **CLI (nivel basico):** log estruturado JSON + `configure_agno_logging` no mesmo
   formato. Suba **Langfuse ou SigNoz local** (Docker) e exporte OTLP para ver traces.
   Custo zero, ganho grande de entendimento.
2. **AgentOS:** ligue `tracing=True` e inspecione traces/sessions pela UI.
3. **Lambda:** adote **aws-lambda-powertools** (Logger/Metrics/Tracer) + **ADOT layer**
   para os spans OTel do Agno; ative `Tracing: Active` no `template.yaml`.
4. **Producao (CloudWatch + X-Ray, obrigatorio):** logs em CloudWatch Logs, traces
   em X-Ray (ADOT), metricas em CloudWatch (EMF) e alarmes CloudWatch nos SLAs;
   sampling + redacao de PII. Os backends locais (SigNoz/Grafana/Langfuse) ficam
   para tras — nao acompanham o agente ate producao.

## Checklist de observabilidade

- [ ] Log estruturado JSON de uma linha, um evento por requisicao.
- [ ] `configure_agno_logging` apontando para o mesmo logger/formato.
- [ ] `request_id` no log E no span (correlacao trace<->log).
- [ ] `debug_mode` ligado so em dev; desligado em producao.
- [ ] Tracing OTel exportando (dev: OTLP para SigNoz/Jaeger/Langfuse; **prod: X-Ray**).
- [ ] Producao usa **exclusivamente CloudWatch (logs/metricas) + X-Ray (traces)**;
      backends locais nao vao para producao.
- [ ] Em Lambda: ADOT layer + `Tracing: Active`; flush de spans garantido.
- [ ] Metricas de fallback/latencia/tokens (OTel ou EMF) + alarmes nos SLAs.
- [ ] Sem PII/segredos em logs ou traces; sampling configurado em producao.
- [ ] Observabilidade so em `infrastructure`/`composition_root`; dominio limpo.
- [ ] Se houver RAG: spans/metricas de retrieval ligados por env (`RAG_TRACING_ENABLED`,
      `RAG_METRICS_ENABLED`); conteudo de chunk (`RAG_TRACE_CONTENT`) so em dev.
