---
name: agno-development-master
description: Guia mestre para estudar e desenvolver multiplos agentes Agno isolados. Use quando o pedido envolver criar, organizar ou revisar qualquer agente Agno neste workspace de estudo.
---

# Agno Development Master

Guia mestre para o workspace de estudo de agentes Agno. O objetivo e desenvolver
varios agentes diferentes, cada um isolado (sem dependencia entre si), para estudar
e entender cada conceito de forma independente.

## Principios do workspace

1. Um agente = um projeto isolado. Cada agente vive em `agents/<nome>/` com seu
   proprio `pyproject.toml` e `.venv` gerenciados por `uv`. Nunca compartilhe venv,
   dependencias ou imports entre agentes.
2. Progressao por niveis. Os agentes seguem os modulos do curso, do mais simples ao
   mais complexo. Comece sempre pelo nivel minimo que resolve o exercicio.
3. Reprodutivel e documentado. Cada agente tem um `README.md` explicando o conceito
   estudado, como rodar, e o que observar.
4. Agno v2. Use a API da Agno v2 (Python 3.9+). Imports principais:
   `from agno.agent import Agent`, `from agno.models.<provider> import <Model>`,
   `from agno.db.sqlite import SqliteDb`, `from agno.knowledge.knowledge import Knowledge`,
   `from agno.vectordb.lancedb import LanceDb`, `from agno.team import Team`,
   `from agno.workflow import Workflow, Step`, `from agno.os import AgentOS`.

## Niveis de tecnologia (mapa dos modulos)

| Nivel | Tema | Skill |
|-------|------|-------|
| 0 | Setup de ambiente (uv, .env, chaves) | `agno-environment-setup` |
| 0 | Estrutura de codigo por agente | `agno-project-structure` |
| 0 | Arquitetura de projeto + SOLID (estrutura de pastas, camadas, refatoracao) | `agno-project-architecture-solid` |
| 1 | Agentes basicos: Run/Stream, Top P/K, temperature, max tokens, context engineering | `agno-basic-agents` |
| 2 | Tools | `agno-tools` |
| 3 | Conhecimento e RAG (LanceDB, embeddings, documentos) | `agno-knowledge-rag` |
| 4 | Memoria e persistencia (Storage, Memory, Sessions) | `agno-memory-sessions` |
| 5 | Agentes multimodais | `agno-multimodal` |
| 6 | Reasoning e raciocinio (reasoning models, ReasoningTools, CoT) | `agno-reasoning` |
| 7 | Teams multi-agente (lider, delegacao, times de times) | `agno-teams` |
| 8 | Workflows basicos (Step, Workflow, ordenacao) | `agno-workflows-basic` |
| 9 | Workflows avancados (Parallel, Condition, Loop, Router) | `agno-workflows-advanced` |
| 10 | Avaliacao e AgentOS (API FastAPI, tracing, deploy AWS EC2) | `agno-agentos-eval-deploy` |
| 11 | Deploy serverless: HTTP REST via Lambda + API Gateway (SAM, Layers) | `agno-lambda-apigateway` |
| * | Observabilidade: tracing OTel, log estruturado, metricas (local + AWS) | `agno-observability` |
| * | Secrets/config: pipe de fallback env -> Secrets Manager, fail fast | `agno-secrets` |
| * | Prompt system: melhores praticas + especificidades por modelo | `agno-prompt-system` |
| * | Providers: adicionar modelos (Gemini, Grok, Kimi K3, Bedrock), tudo por env + default | `agno-providers` |
| * | Guardrails: baseline obrigatorio (PII/injection/moderation) + escopo + Bedrock | `agno-guardrails` |
| * | Security: producao em 2 niveis (agente/LLM + REST API FastAPI), defense in depth | `agno-security` |
| * | AWS: deploy via SAM (template, otimizacao de pacotes) + boto3 recomendado | `agno-aws` |

## Fluxo de trabalho ao criar um novo agente de estudo

1. Escolha o nivel/conceito (ex.: "tools", "rag", "teams", "workflow").
2. Crie `agents/<nome-conceito>/` seguindo `agno-project-structure`.
3. Configure o isolamento com `uv` conforme `agno-environment-setup`.
4. Implemente o agente usando a skill do nivel correspondente.
5. Escreva o `README.md` do agente explicando o que se estuda ali.
6. Rode e valide localmente antes de considerar concluido.

## Providers de modelo suportados nos exemplos

- OpenAI - padrao das docs Agno (`OPENAI_API_KEY`).
- Groq - rapido/barato para estudo (`GROQ_API_KEY`).
- Ollama - modelos locais, sem chave/custo.
- Google Gemini - `GOOGLE_API_KEY` (multimodal forte).
- xAI Grok - `XAI_API_KEY`.
- Moonshot Kimi K3 - `MOONSHOT_API_KEY` (raciocina por padrao).
- AWS Bedrock - credenciais via cadeia AWS + regiao (sem `*_API_KEY`).

Sempre deixe o provider e o modelo configuraveis por variavel de ambiente, nunca
hardcoded, e cada parametro do provider com default se a env faltar (ver skill
`agno-providers`).

## Boas praticas transversais

- Todo agente SEMPRE tem pelo menos um guardrail configurado (ver `agno-guardrails`);
  um agente sem guardrail e considerado incompleto.
- Nunca commitar segredos. Use `.env` + `.env.example`; adicione `.env` ao `.gitignore`.
- Fixe versoes de dependencias (`agno==<versao>`), evitando ranges abertos.
- Um `main.py` minimo e legivel por agente; logica reutilizavel em modulos pequenos.
- Prefira codigo explicito e comentado (e material de estudo, nao producao enxuta).
- Escreva um teste simples por agente quando fizer sentido (smoke test do run).
