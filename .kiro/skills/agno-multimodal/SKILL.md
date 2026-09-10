---
name: agno-multimodal
description: Agentes multimodais em Agno — entrada/saída com imagem, áudio e outros modais, e como juntar todos os modais em um único agente. Use no módulo de Agentes Multimodal.
---

# Agno Multimodal (Nível 5)

Agentes que recebem e/ou produzem além de texto: imagem, áudio, etc.
Requer um modelo que suporte o modal (ex.: OpenAI `gpt-4o` para visão).

## Entrada com imagem

```python
from agno.agent import Agent
from agno.media import Image
from config import get_model  # use um modelo com visão, ex.: gpt-4o

agent = Agent(model=get_model(), markdown=True)

agent.print_response(
    "Descreva o que há nesta imagem.",
    images=[Image(url="https://example.com/foto.jpg")],
    stream=True,
)
```

Imagem local:
```python
agent.print_response(
    "O que está escrito nesta imagem?",
    images=[Image(filepath="data/print.png")],
)
```

## Entrada com áudio

```python
from agno.media import Audio

with open("data/fala.wav", "rb") as f:
    audio_bytes = f.read()

agent.print_response(
    "Transcreva e resuma este áudio.",
    audio=[Audio(content=audio_bytes, format="wav")],
)
```

## Juntar todos os modais em um agente

Um agente pode aceitar múltiplos modais na mesma chamada, desde que o modelo suporte:

```python
agent.print_response(
    "Compare o que vejo na imagem com o que ouço no áudio.",
    images=[Image(filepath="data/grafico.png")],
    audio=[Audio(content=audio_bytes, format="wav")],
    stream=True,
)
```

## Boas práticas

- Confirme que o **modelo escolhido suporta o modal** (visão/áudio) antes de usar.
- Verifique tamanho/formato dos arquivos; documente formatos aceitos no README.
- Guarde mídias de teste em `data/` e adicione ao `.gitignore` se forem grandes.
- Trate erros de mídia (arquivo ausente, formato inválido) com mensagem clara.

## Exercícios sugeridos

1. Descrever uma imagem local e uma por URL.
2. Transcrever um áudio curto.
3. Combinar imagem + áudio + texto numa única pergunta.
