---
name: agno-teams
description: Teams multi-agente em Agno - criar times de agentes com um lider que delega, reasoning no lider e times de times. Use no modulo de Teams Multi-Agente.
---

# Agno Teams (Nivel 7)

Um Team coordena varios agentes. Um lider (LLM) decide dinamicamente para quem
delegar cada parte da tarefa. Ideal para tarefas abertas/criativas.

## Criar um time

```python
from agno.agent import Agent
from agno.team import Team
from config import get_model

writer = Agent(name="Writer", model=get_model(), role="Escreve rascunhos claros.")
editor = Agent(name="Editor", model=get_model(), role="Revisa clareza e tom.")
fact_checker = Agent(name="Fact Checker", model=get_model(), role="Verifica fatos.")

content_team = Team(
    name="Content Team",
    model=get_model(),          # o modelo do LIDER
    members=[writer, editor, fact_checker],
    instructions="Coordene os membros para um texto revisado e verificado.",
    markdown=True,
)

content_team.print_response("Escreva um paragrafo sobre agentes de IA.", stream=True)
```

- Cada membro tem um `role` claro: e como o lider decide a delegacao.
- O `model` do `Team` e o do lider; os membros tem seus proprios modelos.

## Reasoning no lider

Ativar raciocinio no lider melhora a decisao de delegacao.

```python
from agno.tools.reasoning import ReasoningTools

content_team = Team(
    name="Content Team",
    model=get_model(),
    members=[writer, editor, fact_checker],
    tools=[ReasoningTools(add_instructions=True)],
    show_members_responses=True,   # ver o que cada membro respondeu (estudo)
    markdown=True,
)
```

## Times de times (empresa inteira com IA)

Um Team pode ter outro Team como membro, formando hierarquias.

```python
research_team = Team(name="Research", model=get_model(), members=[web_agent, deep_agent])
content_team = Team(name="Content", model=get_model(), members=[writer, editor])

company = Team(
    name="Company",
    model=get_model(),
    members=[research_team, content_team],   # times como membros
    instructions="Delegue pesquisa ao Research e redacao ao Content.",
    markdown=True,
)
company.print_response("Produza um relatorio sobre o mercado de IA.", stream=True)
```

## Team vs Workflow (o que estudar)

- Team: o lider decide em runtime quem faz o que. Flexivel, menos previsivel.
- Workflow: ordem de passos definida por voce. Previsivel e repetivel.

## Boas praticas

- De a cada membro um `role` especifico e unico.
- Comece com poucos membros; adicione conforme a necessidade.
- Use `show_members_responses=True` no estudo para entender a delegacao.
- Para hierarquias, mantenha as responsabilidades de cada sub-time separadas.

## Exercicios sugeridos

1. Time de 3 agentes (writer/editor/fact-checker) e observar a delegacao.
2. Ligar reasoning no lider e comparar a coordenacao.
3. Montar um time de times e ver a hierarquia funcionando.
