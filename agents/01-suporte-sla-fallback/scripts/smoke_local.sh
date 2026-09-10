#!/usr/bin/env bash
# =============================================================================
# Smoke test LOCAL do Agente 01 (Suporte com SLA e Fallback) — Task 20.6
#
# OPCIONAL / requer serviços locais. Este script roda a CLI ponta a ponta contra
# os serviços do `docker-compose` (Ollama como provider de fallback local) e
# aponta ONDE visualizar os traces no backend de observabilidade escolhido.
#
# Mapeia os critérios de aceite:
#   - Req 15.1: docker-compose sobe LocalStack, Ollama e um backend de
#               observabilidade; a CLI usa o Ollama local como provider.
#   - Req 15.4: AgentOS na porta 7777 reutiliza o composition_root — as runs e os
#               traces do agente ficam observáveis lá.
#
# O que ele faz:
#   1. Garante um `.env.local` (copia de `.env.local.example` se não existir).
#   2. (opcional) Sobe/valida os serviços do Compose e baixa um modelo no Ollama.
#   3. Roda a CLI em modo `run` e `stream`, lendo os endpoints por env
#      (OLLAMA_HOST, OTEL_EXPORTER_OTLP_ENDPOINT) — sem alterar código (Req 15.5).
#   4. Força a falha do provider primário para exercitar o fallback via Ollama.
#   5. Imprime as URLs dos backends de observabilidade e do AgentOS.
#
# IMPORTANTE: por padrão o script NÃO sobe o Compose nem baixa modelos (para não
# assumir uma máquina com Docker/Ollama). Ele espera que os serviços já estejam
# de pé. Use `--up` para pedir que ele mesmo suba o Compose e baixe o modelo.
#
# Uso:
#   bash scripts/smoke_local.sh            # assume serviços já no ar
#   bash scripts/smoke_local.sh --up       # sobe o Compose + baixa o modelo Ollama
#   bash scripts/smoke_local.sh --profile grafana   # backend Grafana em vez de SigNoz
#   bash scripts/smoke_local.sh --model llama3.2:1b # escolhe o modelo do Ollama
#
# Requisitos: Docker + Docker Compose (só com --up), uv, e este projeto. Rode a
# partir da RAIZ do projeto (agents/01-suporte-sla-fallback/).
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Localiza a raiz do projeto (diretório-pai de scripts/) para rodar de qualquer
# lugar sem depender do CWD.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# Defaults e parsing simples de flags.
# ---------------------------------------------------------------------------
DO_UP=0                       # --up: subir o Compose e baixar o modelo
COMPOSE_PROFILE=""            # --profile grafana|langfuse (vazio = SigNoz padrão)
OLLAMA_MODEL="llama3.2:1b"    # modelo leve, bom para smoke local
QUESTION="Como redefino minha senha?"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --up)
      DO_UP=1
      shift
      ;;
    --profile)
      COMPOSE_PROFILE="${2:-}"
      shift 2
      ;;
    --model)
      OLLAMA_MODEL="${2:-}"
      shift 2
      ;;
    --question)
      QUESTION="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,40p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "Flag desconhecida: $1" >&2
      echo "Use --help para ver as opções." >&2
      exit 2
      ;;
  esac
done

# `docker compose` (v2) com perfil opcional.
compose() {
  if [ -n "${COMPOSE_PROFILE}" ]; then
    docker compose --profile "${COMPOSE_PROFILE}" "$@"
  else
    docker compose "$@"
  fi
}

section() {
  echo
  echo "=============================================================================="
  echo ">>> $1"
  echo "=============================================================================="
}

# ---------------------------------------------------------------------------
# 1) Garantir o .env.local (nunca versionado). Copiado do exemplo se ausente.
#    Endpoints vêm daqui por env (Req 15.5) — o código não muda entre local/prod.
# ---------------------------------------------------------------------------
section "1/5  Preparando .env.local (endpoints por env — Req 15.5)"
if [ ! -f ".env.local" ]; then
  cp ".env.local.example" ".env.local"
  echo "Criado .env.local a partir de .env.local.example."
  echo "Ajuste chaves reais nele se quiser um provider primário de nuvem (opcional)."
else
  echo ".env.local já existe — mantido como está."
fi

# Carrega o .env.local no ambiente deste shell (para a CLI e o docker compose).
set -a
# shellcheck disable=SC1091
. "./.env.local"
set +a

# Garante que o Ollama entra na ordem de providers para o smoke local (Req 15.1).
# Deixamos o Ollama como fallback: openai (primário, pode falhar) -> ollama (local).
export MODEL_PROVIDER_ORDER="${MODEL_PROVIDER_ORDER:-openai,ollama}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
echo "MODEL_PROVIDER_ORDER = ${MODEL_PROVIDER_ORDER}"
echo "OLLAMA_HOST          = ${OLLAMA_HOST}"
echo "OTEL_EXPORTER_OTLP_ENDPOINT = ${OTEL_EXPORTER_OTLP_ENDPOINT}"

# ---------------------------------------------------------------------------
# 2) (opcional) Subir os serviços do Compose e baixar um modelo no Ollama.
#    Só com --up, para não assumir Docker/Ollama disponíveis. (Req 15.1)
# ---------------------------------------------------------------------------
section "2/5  Serviços locais (docker-compose + Ollama)"
if [ "${DO_UP}" -eq 1 ]; then
  echo "Subindo os serviços do Compose (perfil: '${COMPOSE_PROFILE:-signoz-padrao}')..."
  compose up -d

  echo "Aguardando o Ollama responder em ${OLLAMA_HOST} ..."
  # Espera simples pelo endpoint de tags do Ollama (até ~60s).
  for _ in $(seq 1 30); do
    if curl -sf "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  echo "Baixando o modelo '${OLLAMA_MODEL}' no Ollama (pode demorar na 1a vez)..."
  compose exec -T ollama ollama pull "${OLLAMA_MODEL}"
else
  echo "Modo sem --up: assumindo que LocalStack, Ollama, AgentOS e o backend de"
  echo "observabilidade JÁ estão no ar (suba com: bash scripts/smoke_local.sh --up)."
  echo "Dica: garanta que o modelo '${OLLAMA_MODEL}' já foi baixado no Ollama."
fi

# ---------------------------------------------------------------------------
# 3) Rodar a CLI em modo run e stream contra os serviços locais (Req 15.1).
#    A CLI usa o composition_root (mesmo caminho da API/AgentOS) e lê os
#    endpoints por env — nada de código muda entre local e produção.
# ---------------------------------------------------------------------------
section "3/5  CLI modo run e stream (fallback via Ollama — Req 15.1)"
echo "Pergunta: ${QUESTION}"
echo
echo "--- run ---"
uv run python -m suporte.interface.cli.main "${QUESTION}" --mode run
echo
echo "--- stream ---"
uv run python -m suporte.interface.cli.main "${QUESTION}" --mode stream
echo

# ---------------------------------------------------------------------------
# 4) Forçar a falha do primário para exercitar o FALLBACK via Ollama.
#    Derrubamos o primário reduzindo o timeout por tentativa a ~zero e/ou usando
#    uma chave inválida; o FallbackLLM então cai no Ollama local. Observe no log
#    estruturado: used_fallback=true e provider=ollama.
# ---------------------------------------------------------------------------
section "4/5  Fallback forçado: primário falha -> Ollama responde (Req 15.1)"
echo "Forçando a falha do provider primário (timeout mínimo + chave inválida)"
echo "para que o fallback caia no Ollama local. Observe used_fallback=true e"
echo "provider=ollama na linha de log estruturado."
echo
OPENAI_API_KEY="sk-invalida-para-forcar-fallback" \
LLM_PER_ATTEMPT_TIMEOUT="0.001" \
  uv run python -m suporte.interface.cli.main "${QUESTION}" --mode run || {
    echo
    echo "(Se todos os providers falharem, a CLI degrada graciosamente e escala —"
    echo " confirme que o Ollama está no ar e o modelo '${OLLAMA_MODEL}' baixado.)"
  }

# ---------------------------------------------------------------------------
# 5) Onde ver os traces e as runs do agente (Req 15.4 + observabilidade).
# ---------------------------------------------------------------------------
section "5/5  Onde visualizar os traces e as runs (Req 15.4)"
if [ "${COMPOSE_PROFILE}" = "grafana" ]; then
  echo "Backend de observabilidade (perfil grafana):"
  echo "  - Grafana (traces via Tempo):  http://localhost:3000"
else
  echo "Backend de observabilidade (perfil padrão SigNoz):"
  echo "  - SigNoz (traces/logs/métricas): http://localhost:3301"
fi
echo "  - Langfuse (se subiu com --profile langfuse): http://localhost:3100"
echo
echo "AgentOS — runs, sessions e traces do agente (Req 15.4):"
echo "  - AgentOS: http://localhost:7777"
echo
echo "Correlacione a linha de log estruturado (campo request_id) com o atributo"
echo "app.request_id do span no backend de tracing para achar o trace da requisição."
echo
echo ">>> Smoke local concluído. Consulte scripts/SMOKE_LOCAL.md para o passo a passo. <<<"
