---
name: agno-lambda-apigateway
description: Boas praticas para expor agentes Agno via HTTP REST com FastAPI (Python) sobre AWS Lambda + API Gateway. Cobre FastAPI + Mangum (adapter ASGI, padrao) e FastAPI em container image (AWS Lambda Web Adapter, alternativa), deploy com AWS SAM e dependencias em Lambda Layers. Use ao criar, empacotar ou fazer deploy de um agente Agno como API serverless FastAPI na AWS.
---

# Agno em AWS Lambda + API Gateway (REST serverless com FastAPI)

Guia para expor agentes Agno como API HTTP REST na AWS usando **FastAPI (Python)**, com
deploy via AWS SAM e bibliotecas (Agno + SDKs de LLM) empacotadas em Lambda Layers.

## Regra de tecnologia: a API e escrita em FastAPI

Todo o codigo desta skill DEVE ser desenvolvido com **FastAPI** para expor a API, e a API
FastAPI e **publicada atras do API Gateway da AWS**. Nao escrevemos handlers Lambda "crus"
que montam `dict` de resposta a mao; escrevemos rotas FastAPI (`@app.post(...)`) e deixamos
o FastAPI cuidar de validacao (Pydantic), serializacao, docs OpenAPI e tratamento de erro.

Existem **dois caminhos** para rodar a mesma app FastAPI em Lambda (escolha um; o codigo da
app e o mesmo nos dois):

| Caminho | Como roda | Quando usar | Trade-off |
|---------|-----------|-------------|-----------|
| **FastAPI + Mangum** (PADRAO) | `Mangum(app)` adapta o ASGI do FastAPI para o evento do Lambda; deps em Lambda Layer | Padrao para a maioria dos agentes; pacote cabe no limite de 250 MB | Simples, serverless puro, cold start baixo |
| **FastAPI em container** (ALTERNATIVA) | Imagem de container no ECR com **AWS Lambda Web Adapter**; o FastAPI roda com `uvicorn` normalmente | Quando `agno` + providers estouram 250 MB (funcao + layers) | Ate 10 GB; build/deploy e cold start um pouco maiores |

Nos dois caminhos, o **agente Agno e criado FORA do request** (no escopo do modulo, junto
com a app FastAPI) para ser reaproveitado entre invocacoes e mitigar cold start. As rotas so
orquestram entrada/saida e chamam `agent.run(...)`.

> AgentOS: o servidor FastAPI embutido do AgentOS e otimo para o **ambiente local** de
> acompanhamento do agente (ver a skill `agno-local-dev-environment`). Para o deploy
> serverless controlamos a nossa propria app FastAPI (com as rotas que quisermos expor) e a
> publicamos via API Gateway pelos dois caminhos acima.

## Arquitetura

```
Cliente HTTP
  -> API Gateway (REST ou HTTP API)
    -> Lambda (handler Python; agente Agno)
      -> Provider LLM (OpenAI/Groq/Bedrock/...)
      -> DynamoDB (sessions/memory, opcional)
      -> Secrets Manager / SSM (chaves de API)
```

Camadas:
- API Gateway: roteamento, autenticacao, throttling, validacao de payload na borda.
- Lambda: roda a app **FastAPI** (via Mangum ou Lambda Web Adapter) que executa o agente;
  stateless entre requests (estado vai para DynamoDB).
- Lambda Layer: dependencias pesadas (agno, fastapi, mangum, openai, groq, boto3 extras).

## Estrutura de projeto (SAM)

```
agents-api/
├── template.yaml            # infra SAM (API Gateway + Lambda + Layer + IAM + DynamoDB)
├── samconfig.toml           # config de deploy (stack, regiao, params)
├── src/
│   └── chat_agent/
│       ├── api.py           # app FastAPI (rotas) + agente (fora do request)
│       ├── main.py          # handler = Mangum(app)  (caminho padrao)
│       └── requirements.txt # deps SOMENTE do codigo (nao das libs pesadas)
├── layers/
│   └── agno_deps/
│       └── requirements.txt # agno + fastapi + mangum + sdks de llm (vira o Layer)
├── events/
│   └── invoke.json          # evento de teste local (sam local, formato API Gateway)
└── tests/
    └── test_api.py          # teste das rotas FastAPI (TestClient, sem rede)
```

No caminho de container (alternativa), troque `main.py`/Layer por um `Dockerfile` com o
AWS Lambda Web Adapter (ver secao "Alternativa: container image").

## App FastAPI + agente (padrao base)

Escrevemos uma app **FastAPI**. O agente e a app vivem no escopo do modulo (criados UMA vez
por container). As rotas so orquestram entrada/saida e chamam `agent.run(...)`. O FastAPI
cuida da validacao (Pydantic), da serializacao e das docs OpenAPI (`/docs`).

```python
# src/chat_agent/api.py
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agno.agent import Agent
from agno.models.openai import OpenAIChat

# --- Criados UMA vez por container (reaproveitados entre invocacoes) ---
_agent = Agent(
    model=OpenAIChat(id="gpt-4o-mini"),
    instructions="Voce e um assistente conciso.",
    markdown=False,
)

app = FastAPI(title="Agno Support API", version="1.0.0")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None   # opcional (para memoria/sessions)


class ChatOut(BaseModel):
    content: str
    session_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    # validacao de tamanho/campos ja foi feita pelo Pydantic (422 automatico)
    try:
        result = _agent.run(body.message, session_id=body.session_id)
    except Exception as exc:  # nao vaze detalhes internos ao cliente
        # log vai para CloudWatch; nao logue segredos nem prompt sensivel
        print(f"erro ao executar agente: {exc}")
        raise HTTPException(status_code=502, detail="falha ao processar a solicitacao")
    return ChatOut(content=result.content, session_id=body.session_id)
```

O handler do Lambda so adapta o ASGI do FastAPI com **Mangum** (caminho padrao):

```python
# src/chat_agent/main.py
from mangum import Mangum

from chat_agent.api import app

# handler que o Lambda invoca; Mangum traduz evento API Gateway <-> ASGI
handler = Mangum(app)
```

No `template.yaml`, o `Handler` da funcao aponta para `main.handler` (ver secao SAM).

Regras:
- Instancie modelo e agente FORA das rotas (no escopo do modulo) para evitar recriar por request.
- Use **Pydantic** para validar payload (tamanho, campos) — rejeite cedo, na borda da rota.
- Nao faca `print` de segredos nem de payloads sensiveis.
- Devolva mensagens de erro genericas ao cliente (`HTTPException`); detalhes so em log.
- Streaming e casos longos: ver a secao "Padroes de resposta HTTP".

## Dependencias em Lambda Layer

As libs pesadas (agno, sdk do provider) vao para um Layer; o codigo da funcao fica leve.

```
# layers/agno_deps/requirements.txt
agno==2.8.0
fastapi==0.115.*
mangum==0.19.*          # adapta ASGI (FastAPI) para o evento Lambda
openai==1.*
# adicione apenas o provider que usar (groq, anthropic, etc.)
```

Empacotamento (o SAM constroi o Layer para o runtime correto):
- Use `sam build --use-container` para compilar deps no ambiente da Lambda
  (evita incompatibilidade de wheels nativas com o Amazon Linux).
- Mantenha `src/chat_agent/requirements.txt` vazio ou so com libs muito leves.
- Layers tem limite: total descompactado da funcao + layers <= 250 MB.
  Se estourar, reduza providers ou migre para container image (ver secao final).

## template.yaml (SAM)

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Agno agent exposto via API Gateway + Lambda

Globals:
  Function:
    Runtime: python3.12
    Timeout: 29            # <= 29s por causa do limite do API Gateway
    MemorySize: 1024       # LLM I/O e leve em CPU; ajuste por perfil de latencia
    Architectures: [arm64] # arm64 = mais barato; garanta wheels compativeis

Resources:
  AgnoDepsLayer:
    Type: AWS::Serverless::LayerVersion
    Properties:
      ContentUri: layers/agno_deps/
      CompatibleRuntimes: [python3.12]
    Metadata:
      BuildMethod: python3.12

  ChatAgentFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/chat_agent/
      Handler: main.handler          # main.py: handler = Mangum(app), app FastAPI
      Layers: [!Ref AgnoDepsLayer]
      Environment:
        Variables:
          LLM_SECRET_NAME: !Sub "${AWS::StackName}-llm-keys"
          SESSIONS_TABLE: !Ref SessionsTable
      Policies:
        - AWSLambdaBasicExecutionRole
        - Statement:
            - Effect: Allow
              Action: secretsmanager:GetSecretValue
              Resource: !Ref LlmKeysSecret
        - DynamoDBCrudPolicy:
            TableName: !Ref SessionsTable
      Events:
        Chat:
          Type: Api
          Properties:
            Path: /chat
            Method: post

  LlmKeysSecret:
    Type: AWS::SecretsManager::Secret
    Properties:
      Name: !Sub "${AWS::StackName}-llm-keys"

  SessionsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: session_id
          AttributeType: S
      KeySchema:
        - AttributeName: session_id
          KeyType: HASH

Outputs:
  ApiUrl:
    Value: !Sub "https://${ServerlessRestApi}.execute-api.${AWS::Region}.amazonaws.com/Prod/chat"
```

## Gestao de chaves de API (por cenario)

Nunca coloque chaves de LLM em texto plano em variaveis de ambiente versionadas.
Escolha por cenario:

- Secrets Manager (recomendado para producao): rotacao, auditoria e cross-account.
  Custo por segredo/mes. Use quando ha varias chaves ou requisitos de compliance.
- SSM Parameter Store (SecureString): mais barato para poucas chaves; bom para
  estudo/projetos pequenos. Sem rotacao automatica nativa.
- Variavel de ambiente cifrada por KMS: mais simples e stateless; aceitavel para
  estudo. Evite para chaves de producao de longa duracao.

Padrao: buscar o segredo UMA vez por container e cachear em memoria.

```python
# src/chat_agent/secrets.py
import functools
import json
import os

import boto3

@functools.lru_cache(maxsize=1)
def load_llm_keys() -> dict:
    name = os.environ["LLM_SECRET_NAME"]
    client = boto3.client("secretsmanager")
    value = client.get_secret_value(SecretId=name)["SecretString"]
    return json.loads(value)   # ex.: {"OPENAI_API_KEY": "..."}

# No modulo, antes de criar o agente:
# os.environ.setdefault("OPENAI_API_KEY", load_llm_keys()["OPENAI_API_KEY"])
```

- `lru_cache` garante uma unica chamada por ciclo de vida do container.
- Conceda no IAM apenas `GetSecretValue` do segredo especifico (least privilege).
- Alternativa gerenciada: AWS Parameters and Secrets Lambda Extension (cache local).
- Considere Amazon Bedrock como provider para evitar gerenciar chaves (usa IAM).

## Estado: sessions e memory (DynamoDB)

Lambda e stateless. Para conversas com continuidade, persista sessions/memory em
DynamoDB (serverless-friendly).

```python
from agno.agent import Agent
from agno.db.dynamo import DynamoDb
from agno.models.openai import OpenAIChat
import os

_db = DynamoDb(table_name=os.environ["SESSIONS_TABLE"])  # usa credenciais do ambiente

_agent = Agent(
    model=OpenAIChat(id="gpt-4o-mini"),
    db=_db,
    add_history_to_context=True,   # continuidade por session_id
)
# No handler: _agent.run(message, session_id=session_id, user_id=user_id)
```

Boas praticas:
- Pre-crie a tabela via SAM (o Agno cria na 1a execucao, mas em IaC voce controla
  billing mode, chaves e permissoes). Prefira PAY_PER_REQUEST para carga irregular.
- IAM da funcao com CRUD apenas na tabela usada (`DynamoDBCrudPolicy`).
- Isole por `user_id` + `session_id`; nunca misture usuarios sem intencao.

## Padroes de resposta HTTP

### 1. Sincrono simples (padrao)

Cliente chama, Lambda roda o agente e devolve a resposta completa.
- Limite critico: API Gateway tem timeout maximo de 29s. Se o LLM pode passar disso,
  reduza o escopo, troque o modelo, ou use streaming/async.
- Defina `Timeout` da Lambda <= 29 para falhar de forma limpa.
- Bom para respostas curtas/rapidas e a maioria dos casos de estudo.

### 2. Streaming (respostas longas)

Para respostas longas do LLM sem esbarrar nos 29s, use streaming. No FastAPI, exponha uma
rota com `StreamingResponse`; no deploy prefira **container image + Lambda Web Adapter**
(que suporta response streaming) ou uma **Lambda Function URL** com `InvokeMode=RESPONSE_STREAM`.
O REST API Gateway NAO faz streaming.

```python
# rota de streaming na app FastAPI (src/chat_agent/api.py)
from fastapi.responses import StreamingResponse

@app.post("/chat/stream")
def chat_stream(body: ChatIn) -> StreamingResponse:
    def gen():
        for chunk in _agent.run(body.message, stream=True):
            if chunk.content:
                yield chunk.content
    return StreamingResponse(gen(), media_type="text/plain")
```

- No caminho **container + Lambda Web Adapter**, o `uvicorn` roda a app e o streaming
  funciona de ponta a ponta (recomendado para streaming).
- No caminho **Mangum**, o streaming e bufferizado; para SSE/streaming real prefira o
  container ou uma Function URL dedicada com `InvokeMode: RESPONSE_STREAM`.
- Use quando a UX se beneficia de tokens aparecendo aos poucos.

### 3. Assincrono (agentes lentos: 202 + polling/webhook)

Para agentes que passam de 29s (workflows, muitas tools):
- API Gateway recebe o request e enfileira (SQS) ou dispara Lambda async.
- Retorna 202 com um `job_id`.
- Uma Lambda worker processa e grava o resultado no DynamoDB.
- Cliente consulta `GET /jobs/{job_id}` (polling) ou recebe um webhook.

```
POST /chat        -> 202 {"job_id": "..."}     (enfileira em SQS)
Worker Lambda     -> roda o agente, grava resultado no DynamoDB
GET  /jobs/{id}   -> 200 {"status": "done", "content": "..."}
```

Quando usar cada um:
- Sincrono: respostas rapidas (< ~20s). Simples e barato.
- Streaming: respostas longas com boa UX incremental.
- Assincrono: workflows/teams longos, ou quando 29s nao bastam.

## Cold start e performance

- Instancie agente/modelo/db no escopo do modulo (reuso entre invocacoes).
- Cacheie segredos com `lru_cache` (uma chamada por container).
- Importe so o provider usado; imports pesados aumentam o cold start.
- arm64 costuma ser mais barato; valide que as wheels tem suporte arm64.
- Ajuste `MemorySize`: mais memoria = mais CPU = init mais rapida (teste custo x latencia).
- Para latencia previsivel em producao, considere Provisioned Concurrency.
- LLM domina a latencia (I/O de rede); nao adianta superdimensionar CPU.

## Seguranca da API (API Gateway)

- Nunca deixe o endpoint aberto sem controle. Opcoes:
  - API Key + Usage Plan (throttling/quota) para casos simples.
  - Lambda Authorizer ou Cognito/JWT para autenticacao real de usuarios.
  - IAM auth (SigV4) para chamadas servico-a-servico.
- Ative throttling e quotas para limitar custo/abuso.
- Valide o payload (tamanho, campos) no proprio handler; rejeite cedo.
- IAM da funcao com least privilege (so o segredo e a tabela usados).
- Habilite logs/tracing (CloudWatch + AWS X-Ray) para observar cada request.
- Cuidado com PII em logs; nao logue prompts/respostas sensiveis.

## Deploy com SAM

```bash
# 1. Build (use container para compilar deps no ambiente da Lambda)
sam build --use-container

# 2. Primeiro deploy (guiado: cria stack, bucket, salva samconfig.toml)
sam deploy --guided

# 3. Deploys seguintes
sam deploy

# 4. Setar as chaves no segredo (uma vez)
aws secretsmanager put-secret-value \
  --secret-id <stack>-llm-keys \
  --secret-string '{"OPENAI_API_KEY":"sk-..."}'
```

Boas praticas de deploy:
- Fixe versoes no requirements do Layer (`agno==x.y.z`) para builds reprodutiveis.
- Use stacks/params por ambiente (dev/staging/prod) e tabelas separadas.
- Nao versione segredos; injete-os apos o deploy (comando acima ou pipeline).

## Teste local

Como a API e uma app FastAPI, o loop de desenvolvimento mais rapido e rodar a app direto
com `uvicorn` (sem AWS), e usar o `sam local` so para validar o empacotamento Lambda.

```bash
# 1) app FastAPI pura (dev rapido, com /docs em http://localhost:8000/docs)
uvicorn chat_agent.api:app --reload --port 8000
# curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"message":"ola"}'

# 2) testes das rotas sem rede (TestClient do FastAPI)
pytest tests/test_api.py

# 3) validar o empacotamento no runtime da Lambda
sam local start-api
# curl -X POST http://localhost:3000/chat -d '{"message":"ola"}'
sam local invoke ChatAgentFunction -e events/invoke.json
```

- Para o ambiente local completo (docker-compose com providers/observabilidade/AgentOS),
  ver a skill `agno-local-dev-environment`.
- `events/invoke.json` deve imitar o evento do API Gateway (com `body` string JSON).
- Para chamar o LLM localmente, exporte a chave no ambiente antes de invocar, ou aponte para
  um provider local (ex.: Ollama) durante o desenvolvimento.

## Alternativa: container image (quando o Layer estourar)

Se agno + providers passarem do limite de 250 MB (funcao + layers descompactados), rode a
MESMA app FastAPI como imagem de container no ECR (ate 10 GB), usando o **AWS Lambda Web
Adapter**. O Web Adapter deixa o Lambda invocar uma app HTTP normal, sem Mangum.

```dockerfile
# Dockerfile (caminho container)
FROM public.ecr.aws/lambda/python:3.12
# Lambda Web Adapter: transforma o Lambda em um proxy HTTP para a app
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000 AWS_LWA_INVOKE_MODE=response_stream
COPY requirements.txt ./
RUN pip install -r requirements.txt          # inclui fastapi, uvicorn, agno, provider SDKs
COPY src/ ${LAMBDA_TASK_ROOT}/
# sobe a app FastAPI; o Web Adapter faz o bridge com o Lambda/API Gateway
CMD ["uvicorn", "chat_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

No `template.yaml`, a funcao usa `PackageType: Image` (sem `Handler`/`Layers`):

```yaml
  ChatAgentFunction:
    Type: AWS::Serverless::Function
    Properties:
      PackageType: Image
      Events:
        Chat: { Type: Api, Properties: { Path: /chat, Method: post } }
    Metadata:
      Dockerfile: Dockerfile
      DockerContext: .
```

- A app FastAPI e IDENTICA nos dois caminhos; muda so o empacotamento e o runner
  (Mangum no Layer vs uvicorn + Web Adapter no container).
- O container suporta **response streaming** de ponta a ponta (bom para `/chat/stream`).
- Trade-off: cold start pode ser um pouco maior; build/deploy mais lentos; usa ECR.

## Checklist antes do deploy

- [ ] API escrita como app **FastAPI** (rotas + Pydantic), publicada via API Gateway.
- [ ] Caminho de deploy escolhido: **Mangum** (padrao) ou **container + Web Adapter**.
- [ ] Agente/app instanciados fora do request (escopo do modulo).
- [ ] Chaves via Secrets Manager/SSM, cacheadas; nada hardcoded.
- [ ] Timeout da Lambda <= 29s (ou padrao async/streaming escolhido).
- [ ] IAM least privilege (segredo + tabela especificos).
- [ ] API protegida (auth + throttling).
- [ ] sessions/memory em DynamoDB se precisar de continuidade.
- [ ] Versoes fixadas (Layer com fastapi/mangum) ; `sam build --use-container`.
- [ ] Logs sem PII/segredos; X-Ray habilitado.
- [ ] Testes das rotas FastAPI (TestClient) e `sam local` passando.
