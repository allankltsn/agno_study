---
name: agno-aws
description: Padroes AWS para agentes Agno cujo deploy final e na AWS via AWS SAM (Serverless Application Model) - estrutura de template, otimizacao/empacotamento de artefatos (Layers, build, imagem), IAM least-privilege, e uso recomendado do boto3 (clients reutilizados, paginators, retries/timeouts, tratamento de erro). Use ao modelar infra SAM, empacotar/otimizar o deploy ou escrever qualquer integracao AWS com boto3.
---

# Agno AWS (SAM + boto3)

O projeto tem **deploy final na AWS** e a infra e sempre descrita e publicada com
**AWS SAM**. Toda integracao com servicos AWS usa **boto3** seguindo as praticas
recomendadas. Esta skill define os padroes transversais de AWS; o recorte de
**expor a API** (FastAPI + Lambda + API Gateway, Mangum vs. container) vive na
skill `agno-lambda-apigateway` — aqui focamos em **SAM como padrao de infra**,
**otimizacao de pacotes** e **boto3 bem feito**.

## Principios

1. **IaC sempre via SAM.** Nenhum recurso criado a mao no console; tudo no
   `template.yaml`, versionado e reprodutivel (`sam build` + `sam deploy`).
2. **Least-privilege por padrao.** Cada funcao recebe so as permissoes que usa
   (SAM policy templates ou policy inline minima), nunca `*` em Action/Resource.
3. **Pacote enxuto.** Otimize o artefato: dependencias pesadas em Layers (ou
   imagem), sem arquivos de dev/teste, sem `boto3` no pacote (ja existe no runtime).
4. **Config por ambiente via parametro/env.** Endpoints, nomes de recurso e flags
   vem de `Parameters`/`Environment` do template, nunca hardcoded (skill
   `agno-providers` para o padrao env->default; `agno-secrets` para segredos).
5. **Local espelha prod.** O mesmo template roda contra LocalStack no dev (skill
   `agno-local-dev-environment`); so muda o endpoint por env.

## SAM: estrutura e boas praticas do template

Use `AWS::Serverless-*` (transform SAM) com `Globals` para nao repetir config:

```yaml
# template.yaml
AWSTemplateFormatVersion: "2010-09-09"
Transform: AWS::Serverless-2016-10-31

Parameters:
  AppEnv:
    Type: String
    Default: prod
    AllowedValues: [dev, staging, prod]

Globals:
  Function:
    Runtime: python3.12
    Timeout: 29                 # < timeout do API Gateway (29s) para erro limpo
    MemorySize: 1024            # ajuste por profiling (memoria tambem escala CPU)
    Architectures: [arm64]      # Graviton: mais barato/rapido p/ a maioria das cargas
    Tracing: Active             # X-Ray ligado
    Environment:
      Variables:
        APP_ENV: !Ref AppEnv
        POWERTOOLS_SERVICE_NAME: suporte-sla-fallback

Resources:
  ChatFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/
      Handler: chat_agent.main.handler
      Layers:
        - !Ref DepsLayer
      Policies:                 # least-privilege via policy templates do SAM
        - AWSSecretsManagerGetSecretValuePolicy:
            SecretArn: !Ref ApiKeysSecret
        - DynamoDBCrudPolicy:
            TableName: !Ref SessionsTable
        - S3ReadPolicy:
            BucketName: !Ref PromptsBucket
      Events:
        Api:
          Type: HttpApi          # HTTP API: mais barato/simples que REST API
          Properties:
            Path: /{proxy+}
            Method: ANY

  DepsLayer:
    Type: AWS::Serverless::LayerVersion
    Properties:
      ContentUri: layers/agno_deps/
      CompatibleRuntimes: [python3.12]
    Metadata:
      BuildMethod: python3.12    # SAM instala o requirements.txt do Layer no build

Outputs:
  ApiUrl:
    Value: !Sub "https://${ServerlessHttpApi}.execute-api.${AWS::Region}.amazonaws.com"
```

Boas praticas do template:
- **`Globals`** para runtime/timeout/memoria/tracing comuns; DRY.
- **`arm64` (Graviton)** salvo dependencia sem wheel compativel.
- **HTTP API** em vez de REST API quando nao precisar de features avancadas (WAF
  no REST, contratos, etc.) — mais barato e simples.
- **Timeout da funcao < 29s** (limite do API Gateway) para devolver erro limpo.
- **Policies especificas** (policy templates do SAM) em vez de managed policies amplas.
- **Nunca** coloque segredos no template; referencie ARNs (Secrets Manager) e leia
  em runtime (skill `agno-secrets`).

## Otimizacao de pacotes (artefato enxuto)

Pacote menor = cold start menor, deploy mais rapido e menos superficie.

- **Dependencias pesadas em Layer** (`agno`, `fastapi`, `mangum`, SDK do provider);
  o `requirements.txt` da funcao fica so com o codigo. (Detalhe na skill
  `agno-lambda-apigateway`.)
- **Nao empacote `boto3`/`botocore`**: ja existem no runtime do Lambda. Instale
  boto3 no pacote **apenas** se precisar de uma versao mais nova que a do runtime.
- **`sam build --use-container`** para compilar wheels nativas no ambiente do
  Lambda (evita "funciona na minha maquina").
- **Exclua lixo do artefato**: testes, `__pycache__`, `.env`, docs, datasets.
  Mantenha um `.samignore`/estrutura de `CodeUri` que inclua so `src/`.
- **Pin de versoes** no `requirements.txt` (reprodutibilidade; skill
  `agno-environment-setup`).
- **Container image** (ate 10 GB) so quando `agno` + providers estouram o limite de
  250 MB de funcao+layers (alternativa descrita em `agno-lambda-apigateway`).
- Meca o cold start e o tamanho do pacote; ajuste `MemorySize` por profiling
  (mais memoria = mais CPU, as vezes reduz custo total por ser mais rapido).

## boto3: uso recomendado

### Client reutilizado (fora do handler)
Crie o client **uma vez** no escopo do modulo (reaproveitado entre invocacoes do
mesmo container); nunca por request. Isso reusa conexoes e reduz latencia.

```python
# infrastructure/aws/clients.py
from __future__ import annotations

import os
import boto3
from botocore.config import Config

# Config central: timeouts explicitos + retries com backoff (modo adaptativo).
_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"max_attempts": 3, "mode": "adaptive"},
    user_agent_extra="agno-suporte/1.0",
)

# endpoint_url respeita LocalStack no dev (AWS_ENDPOINT_URL); None em prod.
_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or None


def _client(service: str):
    return boto3.client(service, endpoint_url=_ENDPOINT, config=_BOTO_CONFIG)


# Clients criados UMA vez por processo (reaproveitados entre invocacoes).
secrets_client = _client("secretsmanager")
s3_client = _client("s3")
dynamodb = boto3.resource("dynamodb", endpoint_url=_ENDPOINT, config=_BOTO_CONFIG)
```

### Regras de ouro do boto3
- **Client/resource no escopo do modulo**, nunca dentro do handler/rota.
- **Timeouts + retries explicitos** via `botocore.config.Config` (nao dependa do
  default); use `mode="adaptive"` para throttling.
- **Nunca** passe credenciais no codigo: use a **cadeia de credenciais** (role da
  Lambda em prod; `test/test` no LocalStack em dev). Chaves nunca hardcoded.
- **Paginators** para listagens (`get_paginator`) em vez de paginar a mao.
- **Trate `ClientError`** e inspecione `error["Error"]["Code"]`; converta em erro de
  dominio, sem vazar detalhes ao cliente (skill `agno-security`).
- **Idempotencia**: em create/put, trate "ja existe" como sucesso quando fizer
  sentido (o bootstrap do LocalStack ja faz isso).
- **Import tardio de boto3** nos modulos importaveis sem SDK, quando aplicavel
  (padrao ja usado em `agno-secrets`).

```python
from botocore.exceptions import ClientError
from suporte.infrastructure.aws.clients import secrets_client

def get_secret_string(secret_id: str) -> str | None:
    try:
        resp = secrets_client.get_secret_value(SecretId=secret_id)
        return resp.get("SecretString")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            return None            # ausente => trata como nao encontrado
        raise                      # erro real sobe (logado sem expor segredo)
```

## Fluxo de deploy (SAM)

```bash
sam validate --lint                 # valida o template
sam build --use-container           # build reprodutivel (wheels do Lambda)
sam deploy --guided                 # 1a vez: cria samconfig.toml
# apos configurado:
sam deploy
# desenvolvimento local (contra LocalStack via samlocal, ver agno-local-dev-environment)
sam local invoke ChatFunction -e events/invoke.json
```

Guarde parametros de deploy em `samconfig.toml` (stack name, regiao, capabilities,
parametros por ambiente). CI: `sam build` + `sam deploy --no-confirm-changeset`.

## Servicos AWS usados pelo agente (mapa)

| Servico | Uso no agente | Skill relacionada |
|---------|---------------|-------------------|
| Lambda + API Gateway (HTTP API) | expor a API FastAPI | `agno-lambda-apigateway` |
| Secrets Manager | chaves de API / credenciais | `agno-secrets` |
| S3 | prompts de preferencia | `agno-prompt-system` |
| DynamoDB | sessions/memoria (opcional) | `agno-memory-sessions` |
| Bedrock | provider de LLM / Guardrails | `agno-providers`, `agno-guardrails` |
| CloudWatch / X-Ray | logs, metricas, tracing | `agno-observability` |

## Checklist (deploy AWS via SAM)

- [ ] Toda infra no `template.yaml` (SAM); nada criado a mao no console.
- [ ] `Globals` para runtime/timeout/memoria/tracing; `arm64` quando possivel.
- [ ] IAM least-privilege (policy templates do SAM); sem `*` em Action/Resource.
- [ ] Deps pesadas em Layer; `boto3` fora do pacote (usa o do runtime).
- [ ] `sam build --use-container`; artefato sem testes/`.env`/lixo; versoes pinadas.
- [ ] Timeout da funcao < 29s (API Gateway); `MemorySize` ajustado por profiling.
- [ ] boto3: client no escopo do modulo, timeouts+retries via `Config`, paginators.
- [ ] Credenciais pela cadeia (role em prod, LocalStack no dev); nada hardcoded.
- [ ] Segredos por ARN no template + leitura em runtime (nunca valor no template).
- [ ] Mesmo template roda contra LocalStack no dev (endpoint por env).

## Relacao com outras skills

- `agno-lambda-apigateway`: app FastAPI + handler (Mangum/container) + Layer da API.
- `agno-secrets`: leitura de segredos (Secrets Manager) com fallback e fail fast.
- `agno-security`: IAM least-privilege, sem vazamento, WAF/DDoS na borda.
- `agno-local-dev-environment`: LocalStack espelhando a AWS no dev (mesmo codigo).
- `agno-observability`: CloudWatch/X-Ray como destino OTLP em prod.
- `agno-providers`: padrao env->default para parametros/flags de infra.

Fontes: doc oficial AWS SAM e boto3/botocore. Conteudo parafraseado para
conformidade com restricoes de licenciamento.
