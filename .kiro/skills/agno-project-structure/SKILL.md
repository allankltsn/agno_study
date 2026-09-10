---
name: agno-project-structure
description: Estrutura de código padrão para cada agente Agno de estudo (layout de pastas, arquivos, model factory, README). Use ao criar ou revisar a organização de um agente.
---

# Agno Project Structure

Layout padrão de um agente isolado. Mantém cada agente simples, legível e independente.

## Layout do workspace (raiz)

```
<workspace>/
├── agents/               # um diretório por agente (isolado, deps próprias via uv)
│   └── <nome-agente>/
├── scripts/              # códigos base de uso geral, reaproveitáveis entre agentes
│   ├── README.md
│   ├── setup_code_quality.sh   # configura o tooling de qualidade num agente
│   └── templates/              # configs base (pre-commit, ruff/mypy, import-linter)
├── CHANGELOG.md
└── .kiro/                # skills, specs, steering, hooks
```

A pasta `scripts/` guarda utilitários deterministas e sem lógica de negócio de
nenhum agente específico (setup de ferramentas, templates de configuração). O
tooling de qualidade que esses scripts instalam está detalhado na skill
`agno-code-quality-tools`.

## Layout de pastas (agente)

```
agents/<nome-agente>/
├── pyproject.toml        # deps isoladas (uv)
├── uv.lock
├── .env.example
├── .gitignore
├── README.md             # o que este agente estuda + como rodar
├── main.py               # ponto de entrada (run/stream)
├── config.py             # leitura de env + model factory
├── prompts/
│   └── system_prompt.md  # prompt de preferencia (fonte canonica; enviado ao S3)
└── tests/
    └── test_smoke.py     # smoke test opcional do run
```

Para agentes maiores (RAG, multimodal), adicione módulos por responsabilidade:

```
├── knowledge.py          # setup de Knowledge/vector db (nível RAG)
├── memory.py             # setup de Storage/Memory (nível memória)
└── tools.py              # tools customizadas (nível tools)
```

## Prompts de preferência (bucket S3 + fallback local)

Os prompts de preferência (persona + regras canônicas) são a fonte de verdade e
ficam versionados num **bucket S3**, lidos na montagem. O arquivo em
`prompts/system_prompt.md` é a **fonte canônica do repositório**: no dev, o
bootstrap do LocalStack envia esse arquivo para o bucket; em runtime, o agente lê
do S3 e usa `prompts/system_prompt.md` como **fallback local** se o S3 não estiver
acessível — assim o agente nunca sobe sem prompt. Ver skills `agno-prompt-system`
(leitura do S3) e `agno-local-dev-environment` (criação do bucket no bootstrap).

## config.py — model factory reutilizável

Centralize a escolha de provider aqui para os exemplos ficarem model-agnostic:

```python
import os
from dotenv import load_dotenv

load_dotenv()

def get_model():
    provider = os.getenv("MODEL_PROVIDER", "openai").lower()
    if provider == "openai":
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id="gpt-4o-mini")
    if provider == "groq":
        from agno.models.groq import Groq
        return Groq(id="llama-3.3-70b-versatile")
    if provider == "ollama":
        from agno.models.ollama import Ollama
        return Ollama(id="llama3.2")
    raise ValueError(f"Provider desconhecido: {provider}")
```

## main.py — mínimo e explícito

```python
from agno.agent import Agent
from config import get_model

agent = Agent(
    model=get_model(),
    instructions="Você é um assistente de estudo. Seja claro e didático.",
    markdown=True,
)

if __name__ == "__main__":
    agent.print_response("Explique o que é um agente em uma frase.", stream=True)
```

## README.md do agente (obrigatório)

Cada README deve conter:
1. **Conceito estudado** (ex.: "Streaming e parâmetros de sampling").
2. **Pré-requisitos** (chaves/provider).
3. **Como rodar** (`uv run python main.py`).
4. **O que observar** (o que este exemplo demonstra na prática).

## Regras

- Um único ponto de entrada claro (`main.py`) por agente.
- Não crie abstrações prematuras; cada agente é auto-contido.
- Comente o código pensando em quem estuda, não em produção minimalista.
