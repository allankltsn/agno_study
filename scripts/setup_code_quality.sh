#!/usr/bin/env bash
#
# setup_code_quality.sh — configura o tooling de qualidade de codigo num agente.
#
# Instala as deps de dev (via uv), copia os templates de configuracao que
# faltam, instala os hooks do pre-commit e roda uma verificacao inicial.
# Todas as ferramentas rodam LOCALMENTE, sem gasto de token de LLM.
#
# Uso (a partir da raiz do workspace):
#   bash scripts/setup_code_quality.sh <caminho-do-agente>
#   bash scripts/setup_code_quality.sh agents/01-suporte-sla-fallback
#
# E idempotente: rodar de novo nao sobrescreve config existente nem duplica deps.

set -euo pipefail

# --- resolucao de caminhos ---------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}/templates"

AGENT_DIR="${1:-}"
if [[ -z "${AGENT_DIR}" ]]; then
  echo "Erro: informe o caminho do agente." >&2
  echo "Uso: bash scripts/setup_code_quality.sh <caminho-do-agente>" >&2
  exit 1
fi

if [[ ! -d "${AGENT_DIR}" ]]; then
  echo "Erro: diretorio do agente nao encontrado: ${AGENT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${AGENT_DIR}/pyproject.toml" ]]; then
  echo "Erro: ${AGENT_DIR}/pyproject.toml nao encontrado (nao parece um agente uv)." >&2
  exit 1
fi

echo "==> Configurando qualidade de codigo em: ${AGENT_DIR}"

# --- dependencia: uv ---------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  echo "Erro: 'uv' nao encontrado no PATH. Instale: https://docs.astral.sh/uv/" >&2
  exit 1
fi

# --- 1) deps de dev ----------------------------------------------------------
# Ferramentas que rodam localmente. Idempotente: uv nao duplica entradas.
DEV_DEPS=(
  "pytest>=8.0"
  "pytest-cov>=5.0"
  "hypothesis>=6.0"
  "ruff>=0.6"
  "mypy>=1.11"
  "bandit[toml]>=1.7"
  "pip-audit>=2.7"
  "import-linter>=2.0"
  "radon>=6.0"
  "xenon>=0.9"
  "pre-commit>=3.8"
  "commitizen>=3.29"
)

echo "==> Adicionando dependencias de dev (uv add --group dev)"
( cd "${AGENT_DIR}" && uv add --group dev "${DEV_DEPS[@]}" )

# --- 2) copiar templates que faltam -----------------------------------------
copy_if_absent() {
  local src="$1" dest="$2"
  if [[ -f "${dest}" ]]; then
    echo "    (ja existe, preservado) ${dest}"
  else
    cp "${src}" "${dest}"
    echo "    (criado)                ${dest}"
  fi
}

echo "==> Copiando templates de configuracao (nao sobrescreve existentes)"
copy_if_absent "${TEMPLATES_DIR}/.pre-commit-config.yaml" "${AGENT_DIR}/.pre-commit-config.yaml"

if [[ -f "${AGENT_DIR}/.importlinter" || -f "${AGENT_DIR}/import-linter.toml" ]]; then
  echo "    (ja existe, preservado) contrato do import-linter"
else
  cp "${TEMPLATES_DIR}/import-linter.toml" "${AGENT_DIR}/.importlinter"
  echo "    (criado)                ${AGENT_DIR}/.importlinter"
fi

echo ""
echo "    NOTA: as secoes [tool.ruff], [tool.mypy], [tool.bandit],"
echo "    [tool.importlinter] e [tool.commitizen] devem ser mescladas ao"
echo "    pyproject.toml do agente. Referencia:"
echo "      ${TEMPLATES_DIR}/pyproject.quality.toml"

# --- 3) instalar hooks do pre-commit ----------------------------------------
echo "==> Instalando hooks do pre-commit"
( cd "${AGENT_DIR}" \
    && uv run pre-commit install \
    && uv run pre-commit install --hook-type commit-msg )

# --- 4) verificacao inicial --------------------------------------------------
echo "==> Rodando verificacao inicial (pre-commit run --all-files)"
echo "    (falhas aqui sao esperadas no primeiro run; corrija e recommite)"
( cd "${AGENT_DIR}" && uv run pre-commit run --all-files ) || true

echo ""
echo "==> Concluido. Comandos uteis (rode dentro de ${AGENT_DIR}):"
echo "    uv run ruff check . --fix && uv run ruff format ."
echo "    uv run mypy src"
echo "    uv run bandit -c pyproject.toml -r src"
echo "    uv run pip-audit"
echo "    uv run lint-imports"
echo "    uv run pytest -m 'not e2e and not system and not integration' --cov=<pacote>"
