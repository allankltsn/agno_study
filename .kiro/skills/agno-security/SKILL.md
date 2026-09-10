---
name: agno-security
description: Boas praticas de seguranca para agentes Agno destinados a producao, em dois niveis - seguranca do agente/LLM (OWASP LLM Top 10 - injection, vazamento de dados, uso excessivo) e seguranca da REST API FastAPI (OWASP API Top 10 + itens criticos - authn/authz, JWT validado, hash de senha, refresh token, CSRF, SQLi, upload seguro, rate limiting, validacao, headers, TrustedHost, CORS, TLS, scan de dependencias, WAF/DDoS). Use ao expor um agente em ambiente produtivo ou revisar a postura de seguranca de um agente/API.
---

# Agno Security

Postura de seguranca para um agente que vai para **producao**, em dois niveis:

1. **Nivel do agente/LLM** — proteger o modelo e os dados (OWASP LLM Top 10).
2. **Nivel da REST API (FastAPI)** — proteger a fronteira HTTP (OWASP API Top 10).

Esta skill **amarra** as skills existentes sem duplica-las: guardrails, secrets,
providers e deploy ja cobrem partes; aqui damos o mapa de seguranca de ponta a
ponta e o que falta. Principio geral: **defense in depth** — cada camada assume
que a anterior pode falhar.

## Nivel 1 — Seguranca do agente/LLM

Referencia: OWASP Top 10 para aplicacoes LLM (parafraseado).

### Prompt injection e jailbreak
- Guardrail de prompt injection + escopo **obrigatorios** (skill `agno-guardrails`).
- Trate **toda** entrada do usuario (e conteudo recuperado por RAG, tool outputs,
  documentos) como **nao confiavel**. Instrucoes vindas de dados nao sobrepoem o
  system prompt.
- Separe canais: system prompt (confiavel) vs. input do usuario (nao confiavel);
  nunca concatene input do usuario dentro das `instructions`.

### Vazamento de dados sensiveis
- Guardrail de PII na entrada (skill `agno-guardrails`); considere tambem
  **redacao na saida** se o agente puder ecoar dados sensiveis.
- **Nunca logar** prompt/resposta com PII ou segredos (skill `agno-observability`);
  logue metadados (contagem de tokens, latencia, trigger de guardrail), nao conteudo.
- Segredos nunca no prompt, nunca no codigo (skills `agno-secrets`/`agno-prompt-system`).

### Uso excessivo / negacao de servico e custo
- Limite `max_tokens` por resposta e tamanho maximo do input (truncar/recusar
  entradas gigantes) — controla custo e abuso.
- SLA/timeout e tentativas ja existem no `FallbackLLM`; garanta teto de tentativas
  para nao amplificar custo sob ataque.
- Rate limiting e por request na API (nivel 2), mas o **budget de tokens** e uma
  defesa de nivel de agente.

### Tools e excesso de permissao (agency)
- De a cada tool o **menor privilegio** necessario; nada de tool com acesso amplo
  a filesystem, rede ou credenciais.
- Valide os **argumentos** que o modelo passa para a tool (nao confie no modelo);
  trate a tool como uma fronteira que recebe dados nao confiaveis.
- Prefira operacoes idempotentes/reversiveis; acoes destrutivas exigem confirmacao
  fora do agente.

### Supply chain e modelo
- Fixe versoes de dependencias (skill `agno-environment-setup`); evite ranges abertos.
- Confie apenas em providers/modelos conhecidos; valide o `MODEL_PROVIDER_ORDER`
  contra um registro (o repo ja faz isso em `config.py`).

## Nivel 2 — Seguranca da REST API (FastAPI)

Referencia: OWASP Top 10 para APIs (parafraseado). A API e FastAPI atras do API
Gateway (skill `agno-lambda-apigateway`).

### Autenticacao e autorizacao
- **Nunca** exponha o agente sem authn. Opcoes (por ambiente):
  - API Gateway com **autorizador** (JWT/Lambda authorizer) na frente — 1a barreira.
  - Na app FastAPI, valide o token (OAuth2/JWT) em uma dependency e aplique authz
    por escopo/role.
- Autorize por recurso: um usuario so acessa suas proprias sessions/conversas
  (evita BOLA/IDOR — acesso a objeto de outro usuario por id).

```python
# interface/api/security.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer = HTTPBearer(auto_error=True)

def get_current_principal(cred: HTTPAuthorizationCredentials = Depends(bearer)):
    token = cred.credentials
    try:
        claims = verify_jwt(token)  # valida assinatura, exp, aud, iss
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token invalido")
    return claims  # use claims["sub"]/scopes para authz por recurso
```

### Validacao de entrada (Pydantic)
- Modele o body com Pydantic e **limite tamanho**: `max_length` no texto,
  rejeite payloads acima de um teto. FastAPI valida tipos automaticamente.
- Valide `Content-Type`; recuse o que nao for esperado.

```python
from pydantic import BaseModel, Field

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
```

### Rate limiting e quotas
- Aplique **rate limit** por principal/IP (ex.: API Gateway usage plans + throttling,
  ou middleware como `slowapi` na app). Protege contra abuso e estouro de custo.
- Combine com o budget de tokens do nivel 1.

### Tratamento de erro sem vazamento
- **Nunca** devolva stack trace, mensagem interna ou valor de config ao cliente.
  Responda mensagens neutras; logue o detalhe internamente (sem PII/segredo).
- Bloqueio de guardrail vira uma resposta neutra (skill `agno-guardrails`), nunca
  o motivo interno.

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(docs_url=None, redoc_url=None)  # desabilite docs publicas em prod

@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    # logue exc internamente (sem PII/segredo); resposta neutra ao cliente
    return JSONResponse(status_code=500, content={"detail": "erro interno"})
```

### Headers de seguranca, CORS e TLS
- **HTTPS/TLS obrigatorio** (terminado no API Gateway/ALB). Nunca HTTP em prod.
- **CORS restritivo**: liste origens permitidas explicitamente; nao use `*` com
  credenciais.
- Headers: `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy` (se servir conteudo), `Cache-Control` para respostas
  sensiveis.

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_str("CORS_ALLOWED_ORIGINS", "").split(","),  # explicitas
    allow_credentials=True,
    allow_methods=["POST"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Segredos, dependencias e infraestrutura
- Segredos via `agno-secrets` (env -> Secrets Manager); nunca no codigo/repo/logs.
- IAM de **menor privilegio** para a Lambda (so as acoes/recursos que usa: Bedrock,
  Secrets, S3 de prompts, DynamoDB). Nada de `*` em Action/Resource.
- Fixe versoes; rode auditoria de dependencias (ex.: `pip-audit`) no CI.
- Bucket S3 de prompts: **privado**, sem acesso publico; acesso so pela role da app.

## Itens CRITICOS para FastAPI (obrigatorios em producao)

Estes itens sao **criticos** e devem ser tratados como bloqueadores de deploy.
Baseados no guia [Securing FastAPI Applications (VolkanSah)](https://github.com/VolkanSah/Securing-FastAPI-Applications).
Conteudo parafraseado/resumido para conformidade de licenciamento (guia sob MIT).
Os itens de authn/authz, validacao Pydantic, rate limiting, erro sem vazamento,
CORS/headers/TLS e segredos ja estao descritos acima e tambem sao criticos.

### 1. JWT validado de verdade (nao mock)
- Valide **assinatura, `exp`, `aud`, `iss`** — nunca aceite o token sem verificar.
- Use OAuth2/JWT (`python-jose`) e `WWW-Authenticate: Bearer` no 401.

### 2. Hash de senha forte (se houver credenciais locais)
- **Nunca** senha em texto puro. Use `bcrypt`/`argon2` via `passlib`.
- Se a autenticacao for so por IdP externo (JWT), documente que nao ha senha local.

### 3. Sessao e refresh token
- Access token curto (ex.: 15-30 min) + refresh token com expiracao maior.
- Storage seguro de sessao (Redis/DB); implemente expiracao e revogacao.

### 4. CSRF em operacoes que mudam estado
- Para fluxos com cookie de sessao, habilite CSRF (`fastapi-csrf-protect`).
- APIs puramente Bearer/stateless (sem cookie) tem menor exposicao — documente a escolha.

### 5. Prevencao de SQL injection
- **Sempre** ORM/consulta parametrizada (SQLAlchemy). Nunca concatenar input em SQL.
- (No agente 01, a persistencia e DynamoDB; ainda assim, nunca montar expressao a mao.)

### 6. Seguranca de upload de arquivo (se a API aceitar upload)
- Valide **extensao + tamanho maximo + MIME real** (ex.: `python-magic`), nao so a extensao.
- Gere nome unico (`uuid`), salve **fora do web root**; considere scan de malware.

### 7. Headers de seguranca completos + TrustedHost + redirect HTTPS
- Middleware com `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Strict-Transport-Security`, `Content-Security-Policy`.
- `TrustedHostMiddleware` (allowed hosts) e `HTTPSRedirectMiddleware`.

### 8. Arquivos estaticos e rotas de debug
- Nunca sirva `.env`, `.git`, configs; permissoes corretas no diretorio estatico.
- **Remova rotas de debug/dev** e garanta `debug=False` em producao.

### 9. Logging/monitoramento de eventos de seguranca
- Logue **falhas de login e atividade suspeita** (estruturado, sem PII/segredo/token).
- Health checks (`/health`, `/ready`); alertas para erros criticos e picos de trafego.
  (Log estruturado e OTLP: skill `agno-observability`.)

### 10. Scan de dependencias e supply chain
- Rode `bandit`, `pip-audit` e `safety` no CI; **pin** de versoes; Dependabot/Renovate.
- Falhe o pipeline em vulnerabilidade conhecida.

### 11. Protecoes de borda (infra)
- WAF (regras contra SQLi/XSS), protecao DDoS, apenas portas necessarias abertas.
- No serverless, isso vive no API Gateway/edge (skill `agno-lambda-apigateway`).

> Nota de escopo: `fastapi-csrf-protect`, `passlib`, `python-magic`, `slowapi`,
> `bandit`/`pip-audit`/`safety` sao dependencias adicionais — instale so o que a
> API realmente usar (skill `agno-environment-setup`), fixando versoes.

## Camadas de defesa (ponta a ponta)

```
Cliente
  -> [TLS] API Gateway (authn/authz, rate limit)         # nivel 2
    -> FastAPI (Pydantic, CORS, headers, error handling) # nivel 2
      -> Guardrails pre-hooks (escopo, injection, PII, moderation)  # nivel 1
        -> Agente Agno (budget de tokens, tools least-privilege)    # nivel 1
          -> Providers/Secrets (IAM least-privilege, sem log de segredo)
```

Cada camada assume que a anterior pode falhar (defense in depth).

## Checklist de seguranca (producao)

Agente/LLM:
- [ ] Guardrails obrigatorios ativos (escopo + injection + PII + moderation).
- [ ] Input do usuario, RAG e tool outputs tratados como nao confiaveis.
- [ ] PII/segredos nunca em log; so metadados.
- [ ] `max_tokens` e tamanho maximo de input limitados (custo/abuso).
- [ ] Tools com menor privilegio; argumentos validados.

REST API (FastAPI) — itens criticos:
- [ ] Authn obrigatoria (JWT/authorizer) + authz por recurso (sem BOLA/IDOR).
- [ ] JWT validado de verdade (assinatura, exp, aud, iss) — nunca mock.
- [ ] Hash de senha forte (bcrypt/argon2) se houver credencial local.
- [ ] Access token curto + refresh token; sessao com expiracao/revogacao.
- [ ] CSRF em operacoes com cookie de sessao (ou justificar API stateless Bearer).
- [ ] Validacao Pydantic com limites de tamanho; `Content-Type` checado.
- [ ] SQL injection: ORM/parametrizado; nunca concatenar input.
- [ ] Upload seguro (extensao + tamanho + MIME real, nome unico, fora do web root).
- [ ] Rate limiting por principal/IP + quotas.
- [ ] Erros sem stack trace/vazamento; docs publicas desabilitadas em prod.
- [ ] TLS obrigatorio + redirect HTTP->HTTPS; CORS restritivo; headers completos.
- [ ] TrustedHost (allowed hosts) configurado.
- [ ] Rotas de debug removidas; `debug=False`.
- [ ] Health checks (`/health`, `/ready`); eventos de seguranca logados.

Infra/segredos:
- [ ] Segredos via env -> Secrets Manager; nada no repo/logs; rotacao definida.
- [ ] IAM least-privilege para a Lambda; S3 de prompts privado.
- [ ] Dependencias fixadas + `bandit`/`pip-audit`/`safety` no CI + Dependabot/Renovate.
- [ ] WAF/DDoS na borda; apenas portas necessarias abertas.

## Relacao com outras skills

- `agno-guardrails`: barreira de entrada do agente (nivel 1) — obrigatoria.
- `agno-secrets`: resolucao de segredos sem vazamento.
- `agno-prompt-system`: separacao system vs. input; sem segredos no prompt.
- `agno-observability`: log sem PII/segredo; auditoria de bloqueios.
- `agno-lambda-apigateway`: a API FastAPI atras do API Gateway (nivel 2).
- `agno-providers`: leitura env->default (inclui flags de CORS/limites).

Fontes: OWASP Top 10 para LLM e para APIs; doc do FastAPI (security); guia
[Securing FastAPI Applications (VolkanSah)](https://github.com/VolkanSah/Securing-FastAPI-Applications)
(MIT). Conteudo parafraseado/resumido para conformidade com restricoes de
licenciamento.
