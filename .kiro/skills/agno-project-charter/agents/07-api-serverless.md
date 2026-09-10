# Escopo — Agente 07: API Serverless de Agente (Lambda + API Gateway + SAM)

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 07
- **Nivel**: 11 (deploy serverless)
- **Skills de referencia**: `agno-lambda-apigateway`, `agno-project-architecture-solid`, `agno-environment-setup`
- **Diretorio alvo**: `agents/07-api-serverless/`

## 1. Contexto de negocio

Um agente que so roda no notebook nao entrega valor. A empresa precisa expor o agente
como uma API HTTP confiavel, com custo sob controle (paga pelo uso), ambientes
separados (dev/staging/prod), segredos gerenciados e deploy reprodutivel. Serverless
(Lambda + API Gateway) atende bem cargas variaveis sem manter servidor ligado.

- **Usuario**: outros sistemas/frontends que consomem o agente via HTTP; time de
  plataforma.
- **Dor**: sair do "funciona local" para um servico operavel e reproduzivel.

## 2. Objetivo do estudo

Empacotar e implantar um agente Agno (reusando um dos anteriores, ex.: 01 ou 03) como
API serverless com AWS SAM: handler fino, libs pesadas em Layer, IaC no `template.yaml`,
ambientes isolados, secrets no Secrets Manager e tracing.

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve expor um endpoint HTTP (POST) que recebe a entrada e
  retorna a resposta do agente em JSON.
- **RF-02**: O handler Lambda deve ser fino: traduzir o evento do API Gateway em DTO,
  chamar o caso de uso via `composition_root` e formatar a resposta HTTP.
- **RF-03**: Dependencias pesadas (agno + provider) devem ir em Lambda Layer, fora do
  pacote da funcao.
- **RF-04**: O deploy deve ser reprodutivel via SAM (`sam build` / `sam deploy`), com
  configuracao por ambiente.
- **RF-05**: Segredos (chaves de API) devem vir do Secrets Manager / variaveis do SAM,
  nunca no codigo ou no repo.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Ambientes isolados)**: dev/staging/prod em stacks separadas; nunca
  compartilhar tabela/segredo entre ambientes.
- **RNF-02 (Cold start/custo)**: Instanciar agente/adapters no escopo do modulo
  (reuso entre invocacoes); libs no Layer para builds e deploys rapidos.
- **RNF-03 (Observabilidade)**: Logs no CloudWatch; tracing (X-Ray) ponta a ponta;
  possibilidade de `sam logs --tail`.
- **RNF-04 (Seguranca)**: IAM minimo necessario (least privilege); segredos fora do
  codigo; sem PII em log.
- **RNF-05 (Reprodutibilidade)**: `sam build --use-container` para wheels compativeis
  com o runtime; versoes fixas no Layer.

## 5. Fora de escopo

- Reescrever a logica do agente (reusa um agente ja pronto).
- Streaming/async avancado (documentar como extensao; foco no sync REST).
- CI/CD completo (descrever o pipeline; implementacao opcional).

## 6. Premissas e restricoes

- Conta AWS disponivel (ou uso de `sam local` para validar sem deploy real).
- Agente base ja implementado e testado (ex.: 01 Suporte ou 03 RAG).
- Detalhes de infra seguem `agno-lambda-apigateway` (IAM, timeouts, padroes).

## 7. Arquitetura (camadas + serverless)

- `interface/http/lambda_handler.py`: handler fino (evento -> DTO -> caso de uso -> HTTP).
- `application/` + `domain/`: reaproveitados do agente base (sem AWS, sem Agno direto).
- `infrastructure/`: adapters (Agno, secrets via Secrets Manager, DB se aplicavel).
- `layers/deps/requirements.txt`: libs pesadas (agno + provider) -> Layer.
- `template.yaml` (SAM, na raiz, fora de `src/`): funcao, layer, API, IAM, secrets.
- `samconfig.toml`: config por ambiente.
- `events/invoke.json`: evento de teste para `sam local invoke`.

## 8. Plano de desenvolvimento

1. Escolher o agente base a "vestir" (ex.: 01 ou 03) e portar seu `src/`.
2. Escrever o `lambda_handler` fino + DTO de entrada/saida.
3. Adapter de secrets (Secrets Manager) para as chaves.
4. `template.yaml`: funcao aponta para o handler; Layer com deps; API; IAM minimo.
5. `sam build --use-container` + `sam local invoke` + `sam local start-api`.
6. Deploy `sam deploy --guided`; validar endpoint; `sam logs --tail`.

## 9. Criterios de aceite

- [ ] POST no endpoint retorna a resposta do agente em JSON (RF-01).
- [ ] Handler nao contem regra de negocio (RF-02).
- [ ] Libs pesadas estao no Layer; pacote da funcao e minimo (RF-03, RNF-02).
- [ ] `sam local invoke`/`start-api` funcionam antes do deploy (RF-04).
- [ ] Chaves vem do Secrets Manager/params, nunca do repo (RF-05, RNF-04).
- [ ] Stacks separadas por ambiente (RNF-01); logs/tracing acessiveis (RNF-03).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar por que o handler
e fino, o papel do Layer no ciclo de dev, a separacao de ambientes e as decisoes de
IAM/segredos.
