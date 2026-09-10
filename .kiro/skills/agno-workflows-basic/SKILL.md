---
name: agno-workflows-basic
description: Workflows basicos em Agno - orquestrar Steps em ordem definida com Agents, Teams ou funcoes, usando Workflow e Step. Use no modulo de Workflows Basicos.
---

# Agno Workflows Basicos (Nivel 8)

Um Workflow executa Steps em uma ordem definida por voce. Diferente de um Team
(onde o lider decide em runtime), o Workflow e previsivel e repetivel.

## Conceitos

- Workflow: orquestra os steps em sequencia.
- Step: unidade de trabalho, executada por um Agent, um Team ou uma funcao.
- StepInput: carrega o input original + as saidas dos steps anteriores.
- StepOutput: o que um step retorna (content, stop, success).
- db: persiste o estado da sessao entre steps.

## Workflow sequencial simples

```python
from agno.agent import Agent
from agno.workflow import Step, Workflow
from agno.db.sqlite import SqliteDb
from config import get_model

researcher = Agent(name="Researcher", model=get_model(), role="Pesquisa o tema.")
writer = Agent(name="Writer", model=get_model(), role="Escreve com base na pesquisa.")

pipeline = Workflow(
    name="Research Pipeline",
    db=SqliteDb(db_file="tmp/workflow.db"),
    steps=[
        Step(name="research", agent=researcher),
        Step(name="write", agent=writer),
    ],
)

pipeline.print_response("Escreva sobre agentes de IA.", stream=True)
```

Os steps rodam em ordem: a saida de research vira contexto para write.

## Step com funcao customizada

```python
from agno.workflow import Step, StepInput, StepOutput

def quality_gate(step_input: StepInput) -> StepOutput:
    content = str(step_input.previous_step_content or "")
    if len(content) < 200:
        return StepOutput(content="Muito curto, parando.", stop=True, success=False)
    return StepOutput(content=content, success=True)

# use no Workflow: Step(name="gate", executor=quality_gate)
```

## Acessar saidas anteriores dentro de uma funcao

```python
def meu_step(step_input: StepInput) -> StepOutput:
    original = step_input.input                            # input original
    ultima = step_input.previous_step_content              # saida do step anterior
    especifica = step_input.get_step_content("research")   # saida de step nomeado
    tudo = step_input.get_all_previous_content()           # todas as saidas
    return StepOutput(content="ok")
```

## Team dentro de um Step

```python
Step(name="content", team=content_team)   # um Team inteiro como um step
```

## Boas praticas

- Nomeie cada Step; facilita acessar suas saidas depois.
- Use uma funcao executor para regras deterministicas (validacao, transformacao).
- Persista com db para poder inspecionar o estado da sessao.
- Escolha Workflow quando a ordem importa; Team quando a decisao e dinamica.

## Exercicios sugeridos

1. Pipeline research para write e observar o encadeamento.
2. Adicionar um Step de funcao como quality gate.
3. Trocar um Step de agente por um Step de Team.
