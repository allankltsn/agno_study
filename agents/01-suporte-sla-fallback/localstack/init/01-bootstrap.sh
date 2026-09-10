#!/usr/bin/env bash
#
# 01-bootstrap.sh — Provisiona os recursos AWS que o agente espera no LocalStack.
#
# Este script roda automaticamente quando o LocalStack fica pronto (é montado em
# /etc/localstack/init/ready.d pelo docker-compose). Ele cria, no mínimo:
#   - uma tabela DynamoDB (Req 15.7);
#   - um segredo no Secrets Manager (Req 15.7).
#
# Requisito central: IDEMPOTÊNCIA (Req 15.8). O LocalStack pode reexecutar os
# scripts de init em cada boot do container, e o volume de dados pode persistir
# entre reinícios. Portanto, este script trata "recurso já existe" como SUCESSO —
# ele nunca falha por tentar recriar algo que já está lá.
#
# Usa `awslocal`, o wrapper da AWS CLI que já aponta para o endpoint do LocalStack
# e injeta credenciais fake — não é preciso configurar --endpoint-url manualmente.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuração (com valores padrão sobrescrevíveis por variáveis de ambiente)  #
# --------------------------------------------------------------------------- #
# Nomes de recursos vêm de env para que o mesmo script sirva local e testes de
# sistema, sem editar código. Os defaults refletem o domínio do agente (suporte).
AWS_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-suporte-sla-sessions}"
SECRET_NAME="${SECRET_NAME:-suporte-sla-api-keys}"
# Valor inicial do segredo. Placeholder — nunca um segredo real. O valor de
# verdade é injetado no ambiente/deploy; aqui só garantimos que o segredo existe.
SECRET_INITIAL_VALUE="${SECRET_INITIAL_VALUE:-{\"OPENAI_API_KEY\":\"\",\"GROQ_API_KEY\":\"\"}}"

echo "[bootstrap] Iniciando provisionamento no LocalStack (região=${AWS_REGION})"

# --------------------------------------------------------------------------- #
# 1) Tabela DynamoDB (idempotente)                                            #
# --------------------------------------------------------------------------- #
# Estratégia: consultar describe-table primeiro. Se a tabela já existe, apenas
# pulamos a criação. describe-table retorna erro quando a tabela não existe, por
# isso silenciamos stdout/stderr e testamos o código de saída.
echo "[bootstrap] Verificando tabela DynamoDB '${DYNAMODB_TABLE}'..."
if awslocal dynamodb describe-table \
    --table-name "${DYNAMODB_TABLE}" \
    --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "[bootstrap] Tabela '${DYNAMODB_TABLE}' já existe — nada a fazer (idempotente)."
else
  echo "[bootstrap] Tabela '${DYNAMODB_TABLE}' não existe — criando..."
  # Chave primária simples: 'session_id' (String) como partition key.
  # PAY_PER_REQUEST evita ter de dimensionar throughput no ambiente local.
  awslocal dynamodb create-table \
    --table-name "${DYNAMODB_TABLE}" \
    --attribute-definitions AttributeName=session_id,AttributeType=S \
    --key-schema AttributeName=session_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${AWS_REGION}" >/dev/null
  echo "[bootstrap] Tabela '${DYNAMODB_TABLE}' criada."
fi

# --------------------------------------------------------------------------- #
# 2) Segredo no Secrets Manager (idempotente)                                 #
# --------------------------------------------------------------------------- #
# Estratégia: describe-secret primeiro. Se já existe, não recriamos (recriar
# dispararia ResourceExistsException). Se não existe, criamos com o placeholder.
echo "[bootstrap] Verificando segredo '${SECRET_NAME}'..."
if awslocal secretsmanager describe-secret \
    --secret-id "${SECRET_NAME}" \
    --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "[bootstrap] Segredo '${SECRET_NAME}' já existe — nada a fazer (idempotente)."
else
  echo "[bootstrap] Segredo '${SECRET_NAME}' não existe — criando..."
  awslocal secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --secret-string "${SECRET_INITIAL_VALUE}" \
    --region "${AWS_REGION}" >/dev/null
  echo "[bootstrap] Segredo '${SECRET_NAME}' criado."
fi

echo "[bootstrap] Provisionamento concluído com sucesso."
