---
name: agno-tools
description: Tools em agentes Agno — usar tools prontas, escrever tools customizadas com @tool, e entender tool calling. Use no módulo de Ferramentas e Tools.
---

# Agno Tools (Nível 2)

Tools dão ao agente capacidade de agir (buscar, calcular, chamar APIs).

## Tools prontas

A Agno oferece toolkits. Instale só o que usar (ex.: DuckDuckGo para busca web):

```bash
uv add duckduckgo-search
```

```python
from agno.agent import Agent
from agno.tools.duckduckgo import DuckDuckGoTools
from config import get_model

agent = Agent(
    model=get_model(),
    tools=[DuckDuckGoTools()],
    instructions="Use a busca quando precisar de fatos atuais.",
    markdown=True,
)
agent.print_response("Quais as novidades recentes sobre a Agno?", stream=True)
```

## Tools customizadas

Uma tool é uma função Python bem documentada. O docstring e os type hints
viram a especificação que o modelo enxerga.

```python
from agno.tools import tool

@tool
def celsius_para_fahrenheit(celsius: float) -> float:
    """Converte uma temperatura de Celsius para Fahrenheit.

    Args:
        celsius: temperatura em graus Celsius.
    Returns:
        temperatura em graus Fahrenheit.
    """
    return celsius * 9 / 5 + 32

agent = Agent(model=get_model(), tools=[celsius_para_fahrenheit], markdown=True)
agent.print_response("Quanto é 25°C em Fahrenheit?", stream=True)
```

## Ver as tool calls

Para estudo, mostre o raciocínio/chamadas de tool:

```python
agent = Agent(
    model=get_model(),
    tools=[celsius_para_fahrenheit],
    show_tool_calls=True,   # exibe as chamadas de tool na saída
    markdown=True,
)
```

## Boas práticas de tools

- **Docstring clara** com descrição, `Args` e `Returns` — é o que o modelo lê.
- **Type hints** em todos os parâmetros.
- Uma tool faz **uma coisa**; nomes descritivos.
- Valide entradas e trate erros dentro da tool (retorne mensagem útil).
- Para tools que chamam APIs externas, leia segredos de env, nunca hardcode.

## Exercícios sugeridos

1. Criar uma tool que consulta uma API pública simples.
2. Combinar uma tool pronta (busca) + uma custom.
3. Ligar `show_tool_calls=True` e observar quando/como o modelo decide chamar.
