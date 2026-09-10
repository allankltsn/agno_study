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

## Stack local (desenvolvimento)

Como o Agno fala OTel, aponte o exporter para um coletor local em Docker. Escolha por
objetivo:

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

## AWS (producao)

Mapeamento dos tres sinais para servicos gerenciados. Em Lambda, casa com o padrao de
`agno-lambda-apigateway` (SAM + API Gateway).

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

- Use a **AWS Distro for OpenTelemetry (ADOT) Lambda Layer**. Como o Agno exporta OTLP,
  o ADOT Collector (rodando como extension/layer) encaminha os spans para o X-Ray.
- Ative `Tracing: Active` na funcao SAM (segmento da Lambda + API Gateway correlacionado
  com os spans do Agno).
- Alternativa gerenciada mais nova: **CloudWatch Application Signals** (APM sobre OTel).
- **Cuidado com Lambda:** o export por batch nao combina com o congelamento do container.
  Use o ADOT Collector como extension ou force o flush dos spans ao fim do handler,
  senao voce perde traces.

Trecho de `template.yaml` (SAM):

```yaml
Globals:
  Function:
    Tracing: Active                      # habilita X-Ray na funcao
Resources:
  ChatAgentFunction:
    Type: AWS::Serverless::Function
    Properties:
      Layers:
        # ARN da ADOT layer para a regiao/arquitetura corretas
        - !Sub arn:aws:lambda:${AWS::Region}:901920570463:layer:aws-otel-python-arm64-ver-1-29-0:1
      Environment:
        Variables:
          AWS_LAMBDA_EXEC_WRAPPER: /opt/otel-instrument
          OTEL_SERVICE_NAME: suporte-sla-fallback
      Policies:
        - AWSXRayDaemonWriteAccess       # permissao para enviar ao X-Ray
```

> Confirme o ARN/versao da ADOT layer para a sua regiao e arquitetura (arm64/x86) na
> documentacao oficial; ARNs mudam por regiao e versao.

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
4. **Producao:** sampling + redacao de PII + alarmes CloudWatch nos SLAs.

## Checklist de observabilidade

- [ ] Log estruturado JSON de uma linha, um evento por requisicao.
- [ ] `configure_agno_logging` apontando para o mesmo logger/formato.
- [ ] `request_id` no log E no span (correlacao trace<->log).
- [ ] `debug_mode` ligado so em dev; desligado em producao.
- [ ] Tracing OTel exportando (local: OTLP para SigNoz/Jaeger/Langfuse).
- [ ] Em Lambda: ADOT layer + `Tracing: Active`; flush de spans garantido.
- [ ] Metricas de fallback/latencia/tokens (OTel ou EMF) + alarmes nos SLAs.
- [ ] Sem PII/segredos em logs ou traces; sampling configurado em producao.
- [ ] Observabilidade so em `infrastructure`/`composition_root`; dominio limpo.
