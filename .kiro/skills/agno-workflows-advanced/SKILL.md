---
name: agno-workflows-advanced
description: Workflows avancados em Agno - Parallel, Condition, Loop e Router para logicas complexas de orquestracao. Use no modulo de Workflows Avancados.
---

# Agno Workflows Avancados (Nivel 9)

Componentes de controle de fluxo para pipelines com logica mais complexa.

## Blocos de construcao

1. Step(agent=...)             roda um agente
2. Step(team=...)              roda um team
3. Step(executor=fn)           roda uma funcao
4. Parallel(step1, step2)      roda steps concorrentemente
5. Condition(evaluator, ...)   ramifica com base em logica
6. Loop(steps, ...)            repete ate uma condicao
7. Router(choices, selector)   escolhe dinamicamente qual step rodar

## Parallel (execucao concorrente)

```python
from agno.workflow import Parallel, Step, Workflow

pipeline = Workflow(
    name="Research",
    steps=[
        Parallel(
            "Research",
            Step(name="web_research", agent=web_researcher),
            Step(name="deep_research", agent=deep_researcher),
        ),
        Step(name="analysis", agent=analyst),
    ],
)
```

Os dois pesquisadores rodam ao mesmo tempo; o analyst usa ambas as saidas.

## Condition (ramificacao)

```python
from agno.workflow import Condition, Step, StepInput

def needs_fact_check(step_input: StepInput) -> bool:
    content = str(step_input.previous_step_content or "").lower()
    return any(w in content for w in ["study", "percent", "%", "million"])

Condition(
    name="fact_check_gate",
    evaluator=needs_fact_check,
    steps=[Step(name="fact_check", agent=fact_checker)],
)
```

O step so roda se o evaluator retornar True.

## Loop (repeticao)

```python
from agno.workflow import Loop, Step

Loop(
    name="refine",
    steps=[Step(name="improve", agent=editor)],
    end_condition=lambda outputs: len(str(outputs[-1].content)) > 500,
)
```

## Router (escolha dinamica)

```python
from agno.workflow import Router, Step

def selector(step_input):
    if "codigo" in str(step_input.input).lower():
        return [Step(name="code", agent=coder)]
    return [Step(name="prose", agent=writer)]

Router(name="router", selector=selector,
       choices=[Step(name="code", agent=coder), Step(name="prose", agent=writer)])
```

## Parada antecipada

```python
# de qualquer funcao executor:
return StepOutput(content="Parando.", stop=True)
```

## Boas praticas

- Use Parallel para tarefas independentes (ganho de tempo).
- Use Condition para pular trabalho desnecessario (economia de custo).
- Cuidado com Loop: sempre defina uma end_condition clara para evitar loops infinitos.
- Router e util quando o tipo de tarefa so e conhecido em runtime.

## Exercicios sugeridos

1. Rodar dois pesquisadores em Parallel e medir o tempo vs sequencial.
2. Adicionar uma Condition que pula um step caro.
3. Implementar um Router que escolhe o agente conforme o input.
