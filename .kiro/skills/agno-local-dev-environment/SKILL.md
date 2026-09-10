---
name: agno-local-dev-environment
description: Ambiente de desenvolvimento local para agentes Agno usando docker-compose que simula todas as tecnologias terceiras (LocalStack para AWS, Ollama como provider LLM local, SigNoz ou Grafana+Tempo+Loki+Prometheus para observabilidade OTLP, Langfuse para tracing de LLM) e disponibiliza um AgentOS Agno para acompanhamento do agente. Use ao montar o ambiente local, subir dependencias de terceiros em Docker ou acompanhar um agente pelo AgentOS antes do deploy.
---

# Ambiente de desenvolvimento local (docker-compose + AgentOS)

Guia para montar o ambiente **local** de um agente Agno em que todas as tecnologias
terceiras necessarias sobem via **docker-compose**, e o agente e acompanhado por um
**AgentOS Agno**. O objetivo e reproduzir localmente, sem custo e sem rede externa, o que
em producao seria AWS + provider de LLM + stack de observabilidade.

Ler junto a `agno-environment-setup` (projeto isolado com `uv`), `agno-observability`
(os tres sinais e OTLP) e `agno-lambda-apigateway` (deploy serverless da app FastAPI).

## Principio

O codigo do agente **nao muda** entre local e producao. O que muda sao apenas variaveis de
ambiente (endpoints, chaves) apontando para os servicos do docker-compose local em vez dos
servicos gerenciados da AWS. Isso e o mesmo principio de OTLP da skill de observabilidade:
trocar o destino por env, nunca o codigo.

## O que o docker-compose simula

| Servico local (Docker) | Substitui em producao | Para que serve no estudo |
|------------------------|------------------------|--------------------------|
| **LocalStack** | API Gateway, Lambda, DynamoDB, Secrets Manager, SSM | Testar o deploy serverless (SAM) sem conta AWS |
| **Ollama** | Provider LLM (OpenAI/Groq) | Provider local para fallback offline, custo zero |
| **AgentOS Agno** | (ferramenta de dev) | UI para acompanhar runs, sessions, traces do agente |
| **SigNoz** (perfil padrao) | CloudWatch + X-Ray | Backend OTLP tudo-em-um: traces, logs e metricas |
| **Grafana + Tempo + Loki + Prometheus** (perfil alternativo) | CloudWatch + X-Ray | Os tres sinais separados; alternativa ao SigNoz |
| **Langfuse** | (observabilidade de LLM) | Tracing focado em LLM: custo, prompt/resposta por span |

> Observabilidade: escolha **UM** backend por vez. O `docker-compose` usa **perfis** do
> Compose: o perfil padrao sobe o **SigNoz**; o perfil `grafana` sobe a stack
> **Grafana+Tempo+Loki+Prometheus** no lugar. O Langfuse pode subir junto de qualquer um.

## Estrutura no diretorio do agente

O ambiente local vive dentro do projeto isolado do agente (nao compartilhado):

```
agents/<id>-<nome>/
├── docker-compose.yml          # todos os servicos de terceiros
├── docker-compose.override.example.yml
├── .env.local.example          # endpoints locais (copie para .env.local)
├── otel/
│   └── collector-config.yaml   # config do OpenTelemetry Collector (perfil grafana)
├── grafana/
│   └── datasources.yaml         # Tempo/Loki/Prometheus provisionados no Grafana
├── localstack/
│   └── init/
│       └── 01-bootstrap.sh      # cria tabela DynamoDB, segredo, etc. no LocalStack
├── agentos/
│   └── app.py                   # AgentOS que "veste" o agente para acompanhamento
└── src/ ...                     # o agente (ver agno-project-architecture-solid)
```

## docker-compose.yml

Um unico arquivo com todos os servicos. Observabilidade por **perfis**: sem perfil sobe o
SigNoz; com `--profile grafana` sobe a stack Grafana. Langfuse no perfil `langfuse`.

```yaml
# agents/<id>-<nome>/docker-compose.yml
name: agno-local-dev

services:
  # --- AWS emulada (API Gateway, Lambda, DynamoDB, Secrets, SSM) ---
  localstack:
    image: localstack/localstack:3
    ports:
      - "4566:4566"            # endpoint unico de todos os servicos AWS
    environment:
      SERVICES: "apigateway,lambda,dynamodb,secretsmanager,ssm,iam,logs,cloudformation,sts"
      DEBUG: "0"
    volumes:
      - "./localstack/init:/etc/localstack/init/ready.d"  # scripts de bootstrap
      - "/var/run/docker.sock:/var/run/docker.sock"       # LocalStack Lambda usa Docker

  # --- Provider LLM local (fallback offline, custo zero) ---
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - "ollama-models:/root/.ollama"
    # depois de subir:  docker compose exec ollama ollama pull llama3.2

  # --- AgentOS Agno: UI para acompanhar o agente ---
  agentos:
    build:
      context: .
      dockerfile: agentos/Dockerfile
    ports:
      - "7777:7777"            # UI/API do AgentOS
    env_file:
      - .env.local
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4317"
      OTEL_SERVICE_NAME: "suporte-sla-fallback"
      OLLAMA_HOST: "http://ollama:11434"
    depends_on:
      - ollama

  # --- Observabilidade PADRAO: SigNoz (OTLP tudo-em-um) ---
  # Em producao real o SigNoz e um compose proprio; para estudo, use a distro
  # oficial (clone do signoz/deploy) OU troque pelo perfil grafana abaixo.
  signoz-otel-collector:
    image: signoz/signoz-otel-collector:latest
    profiles: ["signoz", "default"]
    ports:
      - "4317:4317"            # OTLP gRPC (o Agno exporta para ca)
      - "4318:4318"            # OTLP HTTP

  # --- Observabilidade ALTERNATIVA: Grafana + Tempo + Loki + Prometheus ---
  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    profiles: ["grafana"]
    command: ["--config=/etc/otel/collector-config.yaml"]
    volumes:
      - "./otel/collector-config.yaml:/etc/otel/collector-config.yaml"
    ports:
      - "4317:4317"
      - "4318:4318"
    depends_on: [tempo, loki, prometheus]

  tempo:
    image: grafana/tempo:latest
    profiles: ["grafana"]
    command: ["-config.file=/etc/tempo.yaml"]
    ports:
      - "3200:3200"

  loki:
    image: grafana/loki:latest
    profiles: ["grafana"]
    ports:
      - "3100:3100"

  prometheus:
    image: prom/prometheus:latest
    profiles: ["grafana"]
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    profiles: ["grafana"]
    ports:
      - "3000:3000"
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: "Admin"
    volumes:
      - "./grafana/datasources.yaml:/etc/grafana/provisioning/datasources/ds.yaml"
    depends_on: [tempo, loki, prometheus]

  # --- Tracing focado em LLM (opcional, sobe junto de qualquer backend) ---
  langfuse-db:
    image: postgres:16
    profiles: ["langfuse"]
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    volumes:
      - "langfuse-db:/var/lib/postgresql/data"

  langfuse:
    image: langfuse/langfuse:latest
    profiles: ["langfuse"]
    ports:
      - "3001:3000"            # UI do Langfuse (3001 para nao colidir com o Grafana)
    environment:
      DATABASE_URL: "postgresql://langfuse:langfuse@langfuse-db:5432/langfuse"
      NEXTAUTH_SECRET: "dev-secret-nao-usar-em-prod"
      NEXTAUTH_URL: "http://localhost:3001"
      SALT: "dev-salt"
    depends_on: [langfuse-db]

volumes:
  ollama-models:
  langfuse-db:
```

## AgentOS para acompanhamento do agente

O AgentOS e o servidor FastAPI embutido do Agno que serve o agente e uma UI de
acompanhamento (runs, sessions, tools, traces). No ambiente local ele "veste" o mesmo
agente que sera empacotado para producao, sem duplicar o dominio (ver
agno-project-architecture-solid): reusa o composition_root.

```python
# agents/<id>-<nome>/agentos/app.py
from agno.os import AgentOS

# reusa a montagem do agente (composition root do projeto), sem duplicar dominio
from suporte.composition_root import build_agent_for_agentos

agent = build_agent_for_agentos()   # devolve o Agent Agno ja configurado

agent_os = AgentOS(
    agents=[agent],
    # tracing OTel ligado; o endpoint vem por env (OTEL_EXPORTER_OTLP_ENDPOINT)
    # aponta para o collector local (SigNoz ou Grafana) definido no compose
)
app = agent_os.get_app()   # app FastAPI do AgentOS
# roda em container (agentos/Dockerfile) na porta 7777
```

- Ligue tracing=True/debug_mode do Agno so em dev (ver agno-observability).
- A UI fica em http://localhost:7777. Use para inspecionar cada run, ver tokens,
  latencia por span e o caminho de fallback enquanto desenvolve.
- O AgentOS e ferramenta de desenvolvimento/acompanhamento local; o deploy de producao
  usa a app FastAPI + API Gateway (ver agno-lambda-apigateway), nao o AgentOS.

## Variaveis de ambiente (local x producao)

O codigo le sempre as mesmas variaveis; no local elas apontam para o docker-compose.

```dotenv
# agents/<id>-<nome>/.env.local.example  (copie para .env.local)

# Ordem de fallback: no local, provider remoto primario + Ollama local de fallback
MODEL_PROVIDER_ORDER=openai,ollama
OPENAI_API_KEY=            # opcional no local; deixe vazio para so usar Ollama
OLLAMA_HOST=http://localhost:11434

# AWS -> LocalStack (endpoint unico)
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1

# Observabilidade -> collector local (SigNoz padrao ou Grafana)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=suporte-sla-fallback
```

Regra: .env.local nunca e commitado (esta no .gitignore); versione so o
.env.local.example com placeholders. Chaves reais nunca no compose nem no repo.

## Bootstrap do LocalStack

Scripts em localstack/init/*.sh rodam quando o LocalStack fica pronto. Use o awslocal
(CLI do LocalStack) para criar os recursos que o agente espera.

```bash
# localstack/init/01-bootstrap.sh
awslocal dynamodb create-table \
  --table-name sessions \
  --attribute-definitions AttributeName=session_id,AttributeType=S \
  --key-schema AttributeName=session_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

awslocal secretsmanager create-secret \
  --name suporte-llm-keys \
  --secret-string '{"OPENAI_API_KEY":"local-placeholder"}'
```

## Fluxo de trabalho local

```bash
# 1) subir o ambiente (perfil padrao = SigNoz)
docker compose up -d
#    alternativa: stack Grafana em vez do SigNoz
# docker compose --profile grafana up -d
#    somar o Langfuse a qualquer backend
# docker compose --profile langfuse up -d

# 2) baixar um modelo local no Ollama (primeira vez)
docker compose exec ollama ollama pull llama3.2

# 3) rodar o agente (CLI) contra os servicos locais
uv run python -m suporte.interface.cli.main "Como redefino minha senha?"

# 4) acompanhar pelo AgentOS -> UI http://localhost:7777

# 5) observar traces/logs/metricas
#    SigNoz:   http://localhost:3301
#    Grafana:  http://localhost:3000
#    Langfuse: http://localhost:3001

# 6) validar o deploy serverless localmente (SAM contra LocalStack)
#    samlocal deploy --guided

# 7) derrubar tudo
docker compose down
# docker compose down -v          # + apagar volumes (modelos/dados)
```

## Como isso casa com as outras skills

- agno-environment-setup: o projeto continua isolado com uv; o docker-compose so
  adiciona os servicos de terceiros ao redor.
- agno-observability: o Agno exporta OTLP; aqui o destino e um collector local. Trocar
  para AWS (CloudWatch/X-Ray) e so mudar OTEL_EXPORTER_OTLP_ENDPOINT (nao muda codigo).
- agno-lambda-apigateway: a MESMA app FastAPI que roda no AgentOS local e a que vai para o
  Lambda; o LocalStack permite ensaiar o deploy SAM antes da AWS real.

## Checklist do ambiente local

- [ ] docker-compose.yml sobe LocalStack, Ollama, AgentOS e UM backend de observabilidade.
- [ ] Perfis do Compose separam SigNoz (padrao) de Grafana+Tempo+Loki+Prometheus.
- [ ] AgentOS reusa o composition_root do agente (sem duplicar dominio) na porta 7777.
- [ ] .env.local fora do repo; so .env.local.example versionado; sem chaves reais.
- [ ] Endpoints (AWS, OTLP, Ollama) vem por env; codigo do agente nao muda entre local e prod.
- [ ] Ollama com ao menos um modelo baixado para servir de fallback offline.
- [ ] LocalStack com tabela/segredo criados via script de bootstrap.
- [ ] Traces do Agno visiveis no backend escolhido; log estruturado sem PII/segredos.
