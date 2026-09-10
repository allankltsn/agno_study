#!/usr/bin/env bash
#
# 01-bootstrap.sh — Provisiona os recursos AWS que o agente espera no LocalStack.
#
# Este script roda automaticamente quando o LocalStack fica pronto (é montado em
# /etc/localstack/init/ready.d pelo docker-compose). Ele garante:
#   - um segredo no Secrets Manager (Req 15.7);
#   - diagnóstico das tabelas DynamoDB de memory/sessions (o Agno as cria no
#     primeiro uso — ver bloco 1).
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
SECRET_NAME="${SECRET_NAME:-suporte-sla-api-keys}"
# Valor inicial do segredo. Placeholder — nunca um segredo real. O valor de
# verdade é injetado no ambiente/deploy; aqui só garantimos que o segredo existe.
SECRET_INITIAL_VALUE="${SECRET_INITIAL_VALUE:-{\"OPENAI_API_KEY\":\"\",\"GROQ_API_KEY\":\"\"}}"

echo "[bootstrap] Iniciando provisionamento no LocalStack (região=${AWS_REGION})"

# --------------------------------------------------------------------------- #
# 1) Tabelas DynamoDB de memory & sessions                                    #
# --------------------------------------------------------------------------- #
# IMPORTANTE: o Agno (DynamoDb) cria as próprias tabelas de sessions e memories
# no PRIMEIRO uso, com o schema e os índices (GSIs) que ele espera. Por isso NÃO
# criamos essas tabelas aqui — criá-las com um schema divergente (ex.: só
# 'session_id' como chave) quebraria as queries do Agno. Deixamos o Agno
# gerenciar o schema; o agente só precisa de credenciais/endpoint (já providos
# via AWS_ENDPOINT_URL/AWS_REGION) e de permissão para criar/ler/escrever
# (garantida no LocalStack; em produção, ver a policy DynamoDB no template SAM).
#
# Apenas verificamos (sem falhar) se as tabelas esperadas já existem, como
# diagnóstico — o Agno as cria sob demanda se ainda não existirem.
AGENT_DDB_SESSION_TABLE="${AGENT_DDB_SESSION_TABLE:-suporte-sla-sessions}"
AGENT_DDB_MEMORY_TABLE="${AGENT_DDB_MEMORY_TABLE:-suporte-sla-memories}"
for _tbl in "${AGENT_DDB_SESSION_TABLE}" "${AGENT_DDB_MEMORY_TABLE}"; do
  if awslocal dynamodb describe-table \
      --table-name "${_tbl}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "[bootstrap] Tabela DynamoDB '${_tbl}' já existe (gerenciada pelo Agno)."
  else
    echo "[bootstrap] Tabela DynamoDB '${_tbl}' será criada pelo Agno no primeiro uso."
  fi
done

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
