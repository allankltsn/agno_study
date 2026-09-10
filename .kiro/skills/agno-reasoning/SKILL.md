---
name: agno-reasoning
description: Reasoning e raciocínio em agentes Agno — reasoning models, ReasoningTools (think/analyze) e chain-of-thought. Use no módulo de Reasoning e Raciocínio.
---

# Agno Reasoning (Nível 6)

Reasoning dá ao agente passos explícitos de raciocínio antes de responder, melhorando
qualidade em tarefas complexas. A Agno oferece três abordagens.

## 1. Reasoning model (modelo com raciocínio nativo)

Modelos de raciocínio (ex.: o-series da OpenAI) pensam internamente antes de responder.

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat

agent = Agent(
    model=OpenAIChat(id="o3-mini"),   # modelo de reasoning
    markdown=True,
)
agent.print_response("Resolva passo a passo: se 3x+7=22, quanto vale x?")
```

## 2. reasoning=True (chain-of-thought gerenciado pela Agno)

A Agno orquestra um ciclo de raciocínio sobre qualquer modelo.

```python
from agno.agent import Agent
from config import get_model

agent = Agent(
    model=get_model(),
    reasoning=True,                # ativa chain-of-thought
    markdown=True,
)
agent.print_response(
    "Um trem sai às 9h a 60km/h e outro às 10h a 90km/h. Quando o segundo alcança o primeiro?",
    show_full_reasoning=True,      # mostra os passos de raciocínio
)
```

## 3. ReasoningTools (think/analyze como ferramentas)

Dá ao modelo tools explícitas para pensar e analisar. Bom para transparência.

```python
from agno.agent import Agent
from agno.tools.reasoning import ReasoningTools
from config import get_model

agent = Agent(
    model=get_model(),
    tools=[ReasoningTools(add_instructions=True)],
    instructions="Use as ferramentas de raciocínio antes de concluir.",
    markdown=True,
)
agent.print_response(
    "Analise prós e contras de SQLite vs Postgres para um app pequeno.",
    show_full_reasoning=True,
    stream=True,
)
```

## Quando usar cada um (o que estudar)

- **Reasoning model**: melhor qualidade em lógica/matemática; custo maior.
- **reasoning=True**: chain-of-thought em qualquer modelo, sem trocar o provider.
- **ReasoningTools**: quando você quer passos `think`/`analyze` visíveis e controláveis.

## Boas práticas

- Ative reasoning só onde a tarefa exige; em perguntas simples, adiciona custo/latência.
- Use `show_full_reasoning=True` no estudo para inspecionar o passo a passo.
- Compare a mesma pergunta com e sem reasoning e anote a diferença de qualidade.

## Exercícios sugeridos

1. Mesmo problema de lógica com e sem `reasoning=True`.
2. Trocar para um reasoning model e comparar.
3. Adicionar `ReasoningTools` e observar as chamadas `think`/`analyze`.
