# Agente 01 — Suporte com SLA e Fallback de Provider

Assistente de suporte de primeiro nível que responde dúvidas de clientes
fundamentadas na documentação do produto, com resiliência de provider de LLM
(timeout, retry, fallback automático e degradação graciosa).

## 1. O que o agente estuda

Este é o primeiro agente do portfólio de estudo. Ele combina dois objetivos:

- **Agente Agno básico**: persona e regras via `instructions`, documentação do
  produto injetada via `context` (sem RAG), modos de resposta `run` (completa) e
  `stream` (incremental) e parâmetros de sampling.
- **Resiliência de provider**: múltiplos providers de LLM (`openai`, `groq`,
  `ollama`) atrás de uma única abstração `LLMPort`, com **timeout por tentativa**,
  **timeout total**, **retry com backoff**, **fallback automático** para um
  provider secundário e **degradação graciosa** (mensagem clara ao usuário, sem
  stacktrace) quando todos os providers falham.

A decisão de arquitetura central — e o principal material de estudo — é que **o
caso de uso `AnswerQuestion` não sabe que existe fallback**. Para a camada de
aplicação existe apenas uma `LLMPort`. O fallback é um adapter de infraestrutura
(`FallbackLLM`) que implementa essa mesma port e, por dentro, orquestra a lista
ordenada de providers (padrões Composite + Strategy). Os detalhes e a
justificativa dessa decisão estão em `DESENVOLVIMENTO.md`.

Também há perguntas **fora de escopo** (`out_of_scope`): quando a resposta não
está fundamentada na documentação, o agente encaminha a um atendente humano em vez
de alucinar.

O sistema é entregue como **CLI + função chamável** e, adicionalmente, expõe uma
**API HTTP FastAPI** (para deploy serverless) e um **AgentOS** para acompanhamento
local.

## 2. Como rodar (com `uv`)

Pré-requisito: [`uv`](https://docs.astral.sh/uv/) instalado e Python 3.9+.

Todos os comandos abaixo rodam a partir da raiz deste agente
(`agents/01-suporte-sla-fallback/`).

### Preparar o ambiente e as variáveis

```bash
# Instala as dependências no ambiente isolado do projeto (.venv)
uv sync

# Copie o exemplo de configuração e preencha o .env com valores reais
cp .env.example .env
```

Edite o `.env` e informe, no mínimo, a chave do provider primário
(`OPENAI_API_KEY` ou `GROQ_API_KEY`) e a ordem de tentativa em
`MODEL_PROVIDER_ORDER` (ver seção de pré-requisitos). O `.env` **nunca** deve ser
versionado.

### Rodar a CLI

A CLI é a borda de entrada mais simples. Ela recebe a pergunta como argumento
posicional e o modo via `--mode` (`run` ou `stream`):

```bash
# Modo run (resposta completa) — é o padrão
uv run python -m suporte.interface.cli.main "Como redefino minha senha?"

# Modo stream (resposta incremental)
uv run python -m suporte.interface.cli.main "Como redefino minha senha?" --mode stream

# Ajuda / lista de opções
uv run python -m suporte.interface.cli.main --help
```

A pergunta deve ter de 1 a 4000 caracteres; entradas fora desse intervalo ou um
`--mode` inválido são rejeitados na borda, com mensagem de validação, sem chamar o
provider.

### Usar como função chamável

O mesmo caso de uso pode ser montado e chamado em código, pelo `composition_root`
(único lugar que conhece as implementações concretas):

```python
from suporte.composition_root import build_answer_question
from suporte.application.dto import AnswerRequest, Mode

use_case = build_answer_question()
resposta = use_case.execute(AnswerRequest(question="Qual o horário de atendimento?", mode=Mode.RUN))
print(resposta.text, resposta.provider, resposta.total_tokens, resposta.used_fallback)
```

### Rodar a API HTTP (FastAPI + `uvicorn`)

A aplicação FastAPI (`suporte.interface.api.api:app`) reutiliza o **mesmo** caso de
uso da CLI (montado uma vez por processo via `composition_root`) e expõe:

- `POST /chat` — resposta completa (modo `run`); corpo `{ "question": "...", "mode": "run" }`.
- `POST /chat/stream` — resposta incremental (`text/plain` em streaming).
- `GET /health` — verificação de saúde (retorna `200`).
- `GET /docs` — documentação OpenAPI interativa (Swagger UI).

Suba a app localmente com `uvicorn` (servidor ASGI):

```bash
# Sobe a API em http://127.0.0.1:8000 com reload em dev.
# `--reload-dir src` restringe o watch ao codigo-fonte: sem isso o uvicorn
# observa a pasta inteira (incluindo `.venv`) e entra em loop de reload.
uv run uvicorn suporte.interface.api.api:app --reload --reload-dir src

# Exemplos de chamada (em outro terminal)
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Como redefino minha senha?", "mode": "run"}'
```

A validação acontece na borda com Pydantic: `question` fora de `[1, 4000]` ou `mode`
inválido são rejeitados com HTTP `422` **antes** de chamar o caso de uso; um erro de
domínio (todos os providers falharam) vira HTTP `502` genérico, **sem** stacktrace.

**Caminhos de deploy (serverless).** A **mesma** `app` FastAPI é publicada atrás do
AWS API Gateway por dois caminhos, sem mudar o código do agente:

- **Padrão — Mangum + Lambda + Lambda Layer** (`template.yaml`/SAM): o handler
  `suporte.interface.api.main.handler` (`Mangum(app)`) adapta o evento do API Gateway
  à app. É o caminho recomendado.
- **Alternativo — container + AWS Lambda Web Adapter** (`Dockerfile`): a mesma app
  sobe com `uvicorn` e o Web Adapter faz a ponte HTTP↔Lambda. Use quando o pacote
  exceder o limite de 250 MB de Lambda Layers.

Os dois caminhos são **arquivos de infraestrutura**: nenhum faz deploy sozinho. O
"porquê" de cada um está em `DESENVOLVIMENTO.md`.

### Rodar os testes e o roteiro de aceite

```bash
# Suíte de testes (gate rápido de PR)
uv run pytest

# Smoke test determinístico (sem rede e sem chaves)
uv run python scripts/smoke_test.py
```

O roteiro de aceite manual (o que verificar em cada cenário) está em
`scripts/CHECKLIST_ACEITE.md`.

## 3. O que observar

Cada requisição atendida gera **exatamente uma linha de log estruturado (JSON)** e
a CLI imprime, ao final da resposta, um bloco de metadados. Observe:

- **Provider** (`provider`): qual provider efetivamente respondeu. Ao trocar a
  ordem em `MODEL_PROVIDER_ORDER`, este campo deve refletir o novo primário sem
  qualquer mudança de código.
- **Latência** (`latency_ms`): tempo de resposta em milissegundos, útil para
  observar percentis (p95/p99) e o efeito dos timeouts de SLA.
- **Tokens** (`prompt_tokens`, `completion_tokens`, `total_tokens`): custo da
  requisição; `total_tokens` é derivado da soma das parcelas.
- **Fallback** (`used_fallback` e `attempts`): `used_fallback=true` indica que a
  resposta veio de um provider secundário; `attempts` lista os providers tentados
  na ordem. Ao derrubar o primário (chave inválida ou timeout forçado), observe o
  secundário respondendo e o fallback registrado no log.
- **Resultado** (`result`): `"ok"` ou `"error"`.

Privacidade: a pergunta **nunca** aparece em claro no log — apenas como
`question_hash` (SHA-256) — e nenhuma chave de API transita pelos logs.

Se `OTEL_EXPORTER_OTLP_ENDPOINT` estiver configurado, o tracing OpenTelemetry é
exportado para esse endpoint (permitindo correlacionar log e trace pelo
`request_id`); quando ausente/vazio, a exportação é desabilitada sem interromper a
execução.

## 4. Ambiente de desenvolvimento local (Docker Compose + AgentOS)

Para desenvolver e observar o agente localmente sem custo e sem rede externa, o
`docker-compose.yml` sobe as tecnologias de terceiros emuladas: **LocalStack** (AWS:
API Gateway, Lambda, DynamoDB, Secrets Manager, SSM), **Ollama** (provider de LLM
local, fallback custo zero), o **AgentOS** (acompanhamento do agente na porta **7777**)
e a **observabilidade** selecionada por perfil do Compose.

Pré-requisito: Docker + Docker Compose. Copie os placeholders de ambiente antes de
subir:

```bash
cp .env.local.example .env.local
```

### Subir o ambiente e escolher o backend de observabilidade

```bash
# Padrão: LocalStack + Ollama + AgentOS + SigNoz (backend OTLP tudo-em-um)
docker compose up

# Troca o backend de tracing para a stack Grafana (OTel Collector + Tempo + Loki + Prometheus)
docker compose --profile grafana up

# Adiciona o Langfuse (tracing focado em LLM); usa portas próprias, sobe junto
docker compose --profile langfuse up
```

Backends por perfil (Req 15.2):

- **padrão** (sem `--profile`): **SigNoz** — UI em `http://localhost:3301`.
- **`--profile grafana`**: **Grafana** — UI em `http://localhost:3000`.
- **`--profile langfuse`**: **Langfuse** — UI em `http://localhost:3100`.

> **Atenção (conflito intencional de porta OTLP):** o SigNoz (padrão) e o OTel
> Collector (perfil `grafana`) publicam as **mesmas** portas OTLP no host (`4317`
> gRPC e `4318` HTTP). Isso é proposital para garantir um único backend OTLP por vez;
> ative **um** backend de tracing por vez. O `langfuse` usa portas próprias e pode
> subir junto.

### Acessar o AgentOS (porta 7777)

O AgentOS "veste" o agente montado pelo `composition_root` (sem duplicar domínio) e
oferece uma superfície de acompanhamento (runs, sessions, traces) para desenvolvimento
local. Uma vez que o Compose está de pé, acesse:

```
http://localhost:7777
```

O AgentOS é **conveniência de dev** — o deploy de produção usa a app FastAPI + API
Gateway (seção 2), **não** o AgentOS.

### Observabilidade trocada por env (sem mudar código)

O agente lê os endpoints do ambiente (`.env.local.example`): `AWS_ENDPOINT_URL`
(LocalStack), `OLLAMA_HOST` (Ollama) e `OTEL_EXPORTER_OTLP_ENDPOINT` (backend de
tracing). Trocar de SigNoz para Grafana ou apontar para outro collector é só mudar a
variável de ambiente — **o código do agente não muda** entre local e produção.

## 5. Pré-requisitos e limitações

### Pré-requisitos

- **`uv`** e **Python 3.9+** (projeto isolado; sem imports de outros agentes).
- **Chaves de API** dos providers que você pretende usar, definidas no `.env`
  (`OPENAI_API_KEY` e/ou `GROQ_API_KEY`). O provider primário exige a chave
  presente na montagem; a ausência aborta com uma mensagem que **nomeia a variável
  de ambiente faltante**, sem nunca expor o valor.
- **`MODEL_PROVIDER_ORDER`**: ordem de tentativa dos providers, separada por
  vírgula (ex.: `openai,groq`). O primeiro é o primário; os demais são fallback.
- Variáveis de SLA/resiliência (opcionais, com defaults): `LLM_PER_ATTEMPT_TIMEOUT`,
  `LLM_TOTAL_TIMEOUT`, `LLM_MAX_ATTEMPTS`, `LLM_BACKOFF_BASE`.
- Observabilidade (opcional): `OTEL_EXPORTER_OTLP_ENDPOINT` e `APP_ENV`
  (`dev`/`development`/`local` ativa o `debug_mode` do Agno).
- Para o provider local `ollama`, defina `OLLAMA_HOST` apontando para o servidor
  Ollama.

Consulte `.env.example` (e `.env.local.example` para o ambiente Docker local) para
a lista completa de variáveis.

### Limitações

- **Sem RAG**: a documentação do produto é injetada estaticamente via
  `instructions`/`context`. A persona e a FAQ atuais são **placeholders de
  estudo** — substitua por conteúdo real em uso.
- **Sem memória entre sessões**: cada requisição é independente (sem estado).
- **Sem GUI**: as interfaces são a CLI, a função chamável e a API HTTP.
- Deploy em nuvem, RAG, memória e interface gráfica pertencem a agentes
  posteriores do portfólio e estão fora do escopo do núcleo deste agente.
- Documento de estudo: o foco é o **arranjo** (ports/adapters, fallback, DIP,
  observabilidade), não a engenharia de prompt ou a completude da base de
  conhecimento.
