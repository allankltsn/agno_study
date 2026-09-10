---
name: agno-basic-agents
description: Agentes Agno básicos — criar Agent, personalidade/instructions, run vs stream, parâmetros de sampling (temperature, top_p, top_k, max_tokens) e context engineering. Use para os módulos iniciais de agentes.
---

# Agno Basic Agents (Nível 1)

Cobre os primeiros agentes: personalidade, execução (Run/Stream), parâmetros do modelo
e context engineering.

## Criar um agente com personalidade

```python
from agno.agent import Agent
from config import get_model

agent = Agent(
    model=get_model(),
    name="Mentor",
    description="Um mentor técnico paciente.",
    instructions=[
        "Explique conceitos passo a passo.",
        "Use exemplos curtos.",
        "Evite jargão sem definir.",
    ],
    markdown=True,
)
```

- `description`: quem o agente é (persona).
- `instructions`: como ele age (lista de regras claras).

## Run vs Stream

```python
# Run: resposta completa de uma vez
resp = agent.run("Explique streaming de LLM.")
print(resp.content)

# Stream: imprime enquanto gera (melhor UX para estudo)
agent.print_response("Explique streaming de LLM.", stream=True)
```

## Parâmetros de sampling

Configure no modelo (via factory ou direto). Exemplo com OpenAI:

```python
from agno.models.openai import OpenAIChat

model = OpenAIChat(
    id="gpt-4o-mini",
    temperature=0.7,   # criatividade/aleatoriedade (0 = determinístico)
    top_p=0.9,         # nucleus sampling
    top_k=40,          # limita candidatos (quando suportado pelo provider)
    max_tokens=512,    # tamanho máximo da resposta
)
```

O que estudar:
- `temperature` baixa → respostas estáveis; alta → mais variadas.
- `top_p` e `top_k` controlam o espaço de amostragem dos tokens.
- `max_tokens` limita custo e verbosidade.
- Nem todo provider suporta `top_k`; documente no README qual usou.

## Context Engineering

Formas de injetar contexto no agente:

```python
agent = Agent(
    model=get_model(),
    instructions="Responda sempre citando a fonte fornecida.",
    context={"produto": "Kiro IDE", "publico": "devs"},  # contexto estático
    add_datetime_to_context=True,                          # injeta data/hora
    markdown=True,
)
```

Boas práticas:
- Instruções curtas e específicas > prompt gigante.
- Coloque no contexto só o necessário para a tarefa.
- Separe persona (`description`), regras (`instructions`) e dados (`context`).

## Exercícios sugeridos

1. Mesmo prompt com `temperature=0` vs `temperature=1` e compare.
2. Rodar com `stream=True` vs `stream=False`.
3. Alterar `instructions` e observar mudança de comportamento.
