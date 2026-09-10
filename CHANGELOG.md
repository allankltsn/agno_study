# Changelog

Todas as mudancas relevantes deste workspace de estudo de agentes Agno sao
documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o versionamento segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added

- Skill `agno-code-quality-tools`: ferramentas deterministicas de qualidade de
  codigo que rodam em ambiente local (e no CI) sem gasto de token de LLM — Ruff
  (lint/format), mypy (tipos), Bandit/pip-audit/gitleaks (seguranca),
  import-linter (arquitetura em camadas), Radon/Xenon (complexidade),
  pytest-cov/Hypothesis/mutmut (testes) e pre-commit/commitizen (orquestracao),
  com stack minima recomendada, config base e justificativa do gate local.
- Pasta `scripts/` na raiz do workspace com codigos base de uso geral:
  `setup_code_quality.sh` (configura o tooling de qualidade num agente, idempotente)
  e `templates/` (`.pre-commit-config.yaml`, `pyproject.quality.toml`,
  `import-linter.toml`).
- Skill `agno-secrets`: resolucao de config/segredos por pipe de fallback (env
  padrao -> AWS Secrets Manager) com falha rapida quando uma variavel obrigatoria
  esta ausente em todas as fontes.
- Skill `agno-prompt-system`: melhores praticas de prompt de sistema e ajuste de
  estilo por familia de modelo (OpenAI, Groq/Llama, Claude, Gemini, Grok, Kimi K3,
  Bedrock, Ollama).
- Skill `agno-providers`: guia para adicionar providers (Gemini, Grok, Kimi K3,
  Bedrock) com modelo e parametros sempre por variavel de ambiente e default
  pre-definido quando ausente.
- Skill `agno-guardrails`: baseline obrigatorio de guardrails (PII, prompt
  injection, OpenAI Moderation), guardrail de escopo customizado e AWS Bedrock
  Guardrails habilitavel por variavel de ambiente.
- Skill `agno-security`: boas praticas de seguranca para producao em dois niveis
  (agente/LLM seguindo OWASP LLM Top 10 e REST API FastAPI seguindo OWASP API
  Top 10), com checklist e defesa em profundidade. Inclui itens criticos de
  FastAPI (JWT validado, hash de senha, refresh token, CSRF, SQLi, upload seguro,
  TrustedHost, scan de dependencias, WAF/DDoS) baseados no guia VolkanSah.
- Skill `agno-aws`: padroes de deploy na AWS via SAM (estrutura de template,
  IAM least-privilege, otimizacao/empacotamento de artefatos) e uso recomendado
  do boto3 (clients reutilizados, timeouts/retries, paginators, tratamento de erro).
- Agente 01 (suporte-sla-fallback): RAG/Knowledge plugavel por variavel de
  ambiente. `RAG_ENABLED` liga/desliga a base e `RAG_VECTOR_DB` escolhe o backend
  entre LanceDB (local), PgVector (Postgres) e S3Vectors (AWS-native); trocar o
  backend nao altera o codigo do agente. Base desligada por padrao.
- Agente 01: ambiente local ganha o perfil `pgvector` do docker-compose
  (`docker compose --profile pgvector up`) com a extensao pgvector habilitada,
  para desenvolver RAG com PgVector sem servico externo.
- Agente 01: deploy SAM modular por ambiente via `samconfig.toml`
  (`sam deploy --config-env dev|staging|prod`), com RAG e observabilidade
  configuraveis por ambiente sem editar o template.
- Agente 01: memory & sessions persistidos em DynamoDB por padrao (backend
  plugavel por env, `AGENT_DB_BACKEND`, com SQLite como fallback local). Funciona
  no LocalStack em dev e na AWS em producao sem mudar o codigo; a memoria de
  usuario e habilitavel por `AGENT_MEMORY_ENABLED`. O template SAM ganha permissao
  DynamoDB minima e nomes de tabela por ambiente.
- Agente 01: tracing do agente Agno encaminhado ao X-Ray em producao via ADOT
  layer, habilitada e parametrizada por ambiente (`AdotLayerArn` no samconfig),
  com `AWS_LAMBDA_EXEC_WRAPPER` e permissao X-Ray na funcao. Sem ADOT, o X-Ray
  cobre apenas Lambda/API Gateway (trace de infra).

### Changed

- Guia mestre `agno-development-master`: mapa de niveis, lista de providers
  suportados (Gemini, Grok, Kimi K3, Bedrock) e regra transversal de guardrail
  obrigatorio em todo agente.
- `agno-prompt-system`: prompts de preferencia passam a ser lidos de um bucket S3
  (fonte de verdade) com fallback local, para ajustar o comportamento do agente
  sem redeploy.
- `agno-local-dev-environment`: docker-compose/LocalStack passam a provisionar S3
  (bucket de prompts) e Secrets Manager no bootstrap idempotente.
- `agno-project-structure`: arvore de pastas do agente inclui `prompts/system_prompt.md`
  como fonte canonica e fallback local do prompt, e passa a documentar o layout do
  workspace na raiz, incluindo a pasta `scripts/` de codigos de uso geral.
- `agno-code-quality-tools`: ganha a secao "Versionamento" (Conventional Commits +
  SemVer + Keep a Changelog) como fonte de verdade unica desses padroes; o hook
  `changelog-semver-maintainer` passa a referenciar essa secao em vez de duplicar a
  disciplina, eliminando divergencia entre hook e skill.
- `agno-memory-sessions`: enriquecida com memoria factual (User Memories) — os dois
  modos de gestao (automatico `update_memory_on_run` vs agentico
  `enable_agentic_memory`) e seu trade-off de custo, `MemoryManager` customizado
  (modelo barato + instrucoes de extracao), metodos de retrieval
  (`last_n`/`first_n`/`agentic`), tabela de otimizacao de tokens, criterios de
  quando o modo agentico se justifica (com padrao de decisao e modo hibrido) e
  backends gerenciados (Mem0/Zep). Exemplos alinhados a API atual do Agno v2
  (`update_memory_on_run`).
- `agno-knowledge-rag`: passa a cobrir vector DB plugavel por env (LanceDB,
  PgVector, S3Vectors) com trade-offs, alem de ferramentas locais para visualizar
  e medir o RAG (AgentOS, Langfuse, SigNoz/Grafana) e avaliar eficacia com golden
  set (Ragas/DeepEval).
- `agno-testing-strategy`: passa a cobrir otimizacao e busca automatica de
  parametros/modelos/prompt sobre o eval existente - Optuna (hiperparametros de
  sampling, RAG e orquestracao, com pruning/orcamento/Pareto), DSPy (prompt e
  few-shot), promptfoo (comparacao de modelos) e Ragas (eval de RAG), com holdout
  obrigatorio, cache e Ollama para conter custo, marcador `optimization` fora do
  gate de PR e checklist proprio.
- `agno-observability`: adiciona observabilidade especifica de RAG (spans e
  metricas de retrieval) habilitavel por variavel de ambiente, com cuidado de PII
  no conteudo dos chunks. Passa a definir CloudWatch (logs/metricas) + X-Ray
  (traces) como o caminho OBRIGATORIO de observabilidade em producao; os backends
  locais (SigNoz/Grafana/Langfuse) ficam restritos ao desenvolvimento. Esclarece
  que o trace do agente Agno so chega ao X-Ray via ADOT layer (o `Tracing: Active`
  cobre apenas Lambda/API Gateway) e documenta os requisitos e a modularizacao por
  ambiente.
- `agno-memory-sessions`: passa a recomendar DynamoDB como backend preferencial de
  memory & sessions (plugavel por env, SQLite como fallback local), com orientacao
  para deixar o Agno gerenciar o schema das tabelas e para configurar TTL das
  tabelas de session e memory por variavel de ambiente
  (`AGENT_DDB_SESSION_TTL_DAYS`/`AGENT_DDB_MEMORY_TTL_DAYS`).

### Fixed

- `agno-guardrails`: ordem de execucao dos guardrails no exemplo alinhada ao
  padrao recomendado (deterministicos/baratos antes do provider externo).
- `agno-prompt-system`: introducao sincronizada com as familias de modelo cobertas.
- `agno-secrets`: atribuicao da chave resolvida ao ambiente corrige o caso de env
  pre-existente vazia (o SDK passa a enxergar o valor vindo do Secrets Manager).
