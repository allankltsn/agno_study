---
name: agno-environment-setup
description: Setup de ambiente isolado para agentes Agno com uv (um projeto por agente), variáveis de ambiente e chaves de API. Use ao criar um novo agente ou configurar dependências.
---

# Agno Environment Setup

Cada agente é um **projeto Python isolado** gerenciado por `uv`. Isso garante que um
agente não interfira nas dependências de outro.

## Pré-requisitos

- Python 3.9+ (Agno v2 requer 3.9 ou superior).
- `uv` instalado. No Windows (bash/msys):
  ```bash
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  Verifique: `uv --version`.

## Criar um agente isolado

A partir da raiz do workspace:

```bash
uv init agents/<nome-agente>
cd agents/<nome-agente>
uv add agno python-dotenv
```

Dependências por provider (adicione só o que o agente usar):

```bash
# OpenAI
uv add openai
# Groq
uv add groq
# Ollama (roda modelos locais; instale o app Ollama separadamente)
uv add ollama
```

Extras por nível (adicione sob demanda):

```bash
# RAG / vector DB
uv add lancedb tantivy
# AgentOS (servir como API FastAPI)
uv add "agno[fastapi]" uvicorn
```

## Fixar versões

Após adicionar, fixe as versões no `pyproject.toml` para reprodutibilidade
(ex.: `agno==2.x.y`). Rode `uv lock` e mantenha o `uv.lock` versionado.

## Variáveis de ambiente e chaves

Crie `.env` (nunca commitado) e `.env.example` (commitado, sem valores reais):

`.env.example`
```dotenv
# Escolha do provider: openai | groq | ollama
MODEL_PROVIDER=openai

# Chaves (preencha apenas as que usar)
OPENAI_API_KEY=
GROQ_API_KEY=
# Ollama roda local; normalmente não precisa de chave
OLLAMA_HOST=http://localhost:11434
```

Carregue no código com `python-dotenv`:
```python
from dotenv import load_dotenv
load_dotenv()
```

## .gitignore por agente

```gitignore
.venv/
__pycache__/
.env
tmp/
*.db
data/lancedb/
```

## Rodar

```bash
uv run python main.py
```

## Regras

- **Nunca** reutilize o `.venv` de outro agente.
- **Nunca** hardcode chaves; sempre via `os.getenv`.
- Cada agente instala apenas as dependências que realmente usa.
