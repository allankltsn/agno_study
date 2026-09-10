# Smoke test local — CLI contra os serviços do docker-compose (Task 20.6)

> **OPCIONAL / requer serviços locais.** Este roteiro roda o agente ponta a ponta
> contra o `docker-compose` (Ollama como provider de **fallback local**) e mostra
> **onde visualizar os traces** no backend de observabilidade escolhido. Ele **não
> faz parte do gate de PR** — precisa de Docker, Ollama e (opcionalmente) um modelo
> baixado. Sem esses serviços, use o smoke determinístico
> (`scripts/smoke_test.py`), que reproduz os cenários sem rede.

Este roteiro mapeia dois critérios de aceite do Requisito 15:

| Critério | O que exige | Onde aparece neste smoke |
|----------|-------------|--------------------------|
| **Req 15.1** | `docker-compose` sobe LocalStack, **Ollama** (provider de LLM local) e um backend de observabilidade; o agente usa o Ollama local | Passos 2–4: Ollama sobe pelo Compose e responde à CLI (inclusive como fallback) |
| **Req 15.4** | **AgentOS** na porta 7777 reutiliza o `composition_root` e expõe runs, sessions e traces | Passo 6: as runs/traces do agente ficam observáveis em `http://localhost:7777` |
| _(apoio)_ Req 15.5 | Endpoints (`OLLAMA_HOST`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `AWS_ENDPOINT_URL`) vêm de env | Passo 1: `.env.local` — o **código não muda** entre local e produção |

Há duas formas de rodar este smoke:

- **Automatizada:** `bash scripts/smoke_local.sh` (ver seção "Script").
- **Manual passo a passo:** as seções abaixo, para entender cada etapa.

---

## Pré-requisitos

- **Docker + Docker Compose** (v2, comando `docker compose`).
- **uv** instalado (para rodar a CLI: `uv run ...`).
- Rodar a partir da raiz do projeto: `agents/01-suporte-sla-fallback/`.
- Espaço em disco para o modelo do Ollama (use um modelo leve, ex.: `llama3.2:1b`).

> Nenhuma chave de nuvem é obrigatória: o smoke usa o **Ollama local** como
> provider. Uma chave real de OpenAI/Groq é opcional (só se quiser um primário de
> nuvem antes de cair no Ollama).

---

## Passo 1 — Preparar o `.env.local` (endpoints por env, Req 15.5)

Copie o exemplo versionado para o arquivo local (nunca versionado) e ajuste se
quiser:

```bash
cp .env.local.example .env.local
```

Confirme (ou ajuste) as variáveis relevantes ao smoke local:

```dotenv
# Ollama como fallback local; primário de nuvem é opcional.
MODEL_PROVIDER_ORDER=openai,ollama
OLLAMA_HOST=http://localhost:11434
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
APP_ENV=local
```

O agente lê esses endpoints do ambiente — **o mesmo código roda local e em
produção**, mudando só as env vars (Req 15.5).

---

## Passo 2 — Subir os serviços locais (Req 15.1)

Backend de observabilidade **padrão (SigNoz)**:

```bash
docker compose up -d
```

Ou o backend **alternativo (Grafana + Tempo + Loki + Prometheus)**:

```bash
docker compose --profile grafana up -d
```

> Escolha **um** backend de tracing OTLP por vez: SigNoz (padrão) e o OTel
> Collector do perfil `grafana` disputam de propósito a porta OTLP `4317`
> (Req 15.3). O `langfuse` (`--profile langfuse`) usa porta própria e pode subir
> junto.

Isso sobe, entre outros, o **LocalStack**, o **Ollama** (porta `11434`) e o
**AgentOS** (porta `7777`).

---

## Passo 3 — Baixar um modelo no Ollama

O Ollama sobe vazio; baixe um modelo leve para o smoke:

```bash
docker compose exec ollama ollama pull llama3.2:1b
```

Confirme que ele responde:

```bash
curl -sf http://localhost:11434/api/tags
```

---

## Passo 4 — Rodar a CLI contra os serviços locais (Req 15.1)

A CLI usa o `composition_root` (mesmo caminho da API e do AgentOS) e lê os
endpoints por env. Rode nos dois modos:

```bash
# Modo run (resposta completa)
uv run python -m suporte.interface.cli.main "Como redefino minha senha?" --mode run

# Modo stream (resposta incremental)
uv run python -m suporte.interface.cli.main "Como redefino minha senha?" --mode stream
```

Observe, na saída e na **linha de log estruturado (JSON)**: `provider`,
`latency_ms`, tokens e `used_fallback`. A pergunta aparece só como `question_hash`.

---

## Passo 5 — Forçar o fallback via Ollama (Req 15.1)

Para ver o **fallback automático** cair no Ollama local, derrube o provider
primário (timeout mínimo por tentativa e/ou chave inválida) numa única execução:

```bash
OPENAI_API_KEY="sk-invalida-para-forcar-fallback" \
LLM_PER_ATTEMPT_TIMEOUT="0.001" \
  uv run python -m suporte.interface.cli.main "Como redefino minha senha?" --mode run
```

Esperado no log estruturado: `used_fallback=true` e `provider=ollama` (o primário
esgota as tentativas e o `FallbackLLM` cai no Ollama). Se **todos** os providers
falharem, a CLI degrada graciosamente e escala para humano — confirme então que o
Ollama está no ar e o modelo foi baixado (Passo 3).

---

## Passo 6 — Visualizar os traces e as runs (Req 15.4)

- **AgentOS** (runs, sessions e traces do agente, Req 15.4): <http://localhost:7777>
- Backend de observabilidade OTLP:
  - **SigNoz** (padrão): <http://localhost:3301>
  - **Grafana** (perfil `grafana`, traces via Tempo): <http://localhost:3000>
  - **Langfuse** (perfil `langfuse`, opcional): <http://localhost:3100>

Correlacione a requisição entre log e trace pelo `request_id`: o mesmo valor
aparece no campo `request_id` do log estruturado e no atributo `app.request_id`
do span exportado por OTLP. Assim você localiza o trace exato de cada chamada da
CLI no backend escolhido.

> Se `OTEL_EXPORTER_OTLP_ENDPOINT` estiver ausente/vazio, o tracing é desabilitado
> com um aviso no log e a requisição prossegue normalmente (Req 16.2) — a CLI
> continua funcionando, só não haverá spans no backend.

---

## Script (execução automatizada)

O `scripts/smoke_local.sh` encadeia os passos acima. Por padrão ele **não** sobe o
Compose nem baixa modelos (para não assumir Docker/Ollama na máquina): use `--up`
para que ele mesmo suba os serviços e baixe o modelo.

```bash
# Assume que os serviços já estão no ar:
bash scripts/smoke_local.sh

# Sobe o Compose + baixa o modelo do Ollama e então roda o smoke:
bash scripts/smoke_local.sh --up

# Backend Grafana em vez de SigNoz:
bash scripts/smoke_local.sh --up --profile grafana

# Escolher o modelo do Ollama e/ou a pergunta:
bash scripts/smoke_local.sh --up --model llama3.2:1b --question "Como abro um chamado?"
```

Ao final, o script imprime as URLs do AgentOS e do backend de observabilidade
ativo.

---

## Encerrar o ambiente

```bash
docker compose down            # para os serviços (mantém os volumes/modelos)
docker compose down -v         # também remove os volumes (re-download do modelo)
```

---

## Relação com o smoke determinístico

Este roteiro é o **modo ao vivo** do agente contra serviços locais. Para validar
os mesmos cenários de comportamento e de log **sem rede e sem serviços**, use o
smoke determinístico e o checklist de aceite:

- `scripts/smoke_test.py` — cenários do escopo §9 com dublês de `LLMPort`.
- `scripts/CHECKLIST_ACEITE.md` — checklist mapeado ao escopo §9.
