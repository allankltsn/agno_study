---
name: agno-knowledge-rag
description: Conhecimento e RAG em Agno — como LLM/vetores funcionam, criar Knowledge com LanceDB, embeddings e adicionar documentos complexos. Use no módulo de Conhecimento e RAG.
---

# Agno Knowledge & RAG (Nível 3)

RAG (Retrieval-Augmented Generation) dá ao agente acesso a uma base de conhecimento
buscável por similaridade vetorial.

## Conceito (o que estudar)

1. Documentos são divididos em chunks.
2. Cada chunk vira um **embedding** (vetor).
3. Vetores ficam num **vector DB** (aqui, LanceDB — local, sem servidor).
4. Na pergunta, busca-se os chunks mais similares e injeta-se no contexto do LLM.

## Dependências

```bash
uv add lancedb tantivy
# embeddings via OpenAI (ou use outro embedder):
uv add openai
```

## Montar Knowledge com LanceDB

```python
from agno.knowledge.knowledge import Knowledge
from agno.knowledge.embedder.openai import OpenAIEmbedder
from agno.vectordb.lancedb import LanceDb, SearchType

knowledge = Knowledge(
    name="Base de Estudo",
    vector_db=LanceDb(
        table_name="estudo",
        uri="data/lancedb",                 # armazenamento local
        search_type=SearchType.hybrid,      # vetorial + keyword
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
)
```

## Adicionar documentos

```python
# Texto/URL/arquivo — o loader faz chunk + embedding
knowledge.add_content(url="https://docs.agno.com/first-agent")
knowledge.add_content(path="docs/manual.pdf")
```

Documentos complexos (PDF, sites, várias páginas): adicione um por vez e verifique
o que foi carregado antes de consultar.

## Ligar Knowledge ao agente

```python
from agno.agent import Agent
from config import get_model

agent = Agent(
    model=get_model(),
    knowledge=knowledge,
    search_knowledge=True,   # o agente busca na base automaticamente
    instructions="Responda usando a base; se não achar, diga que não sabe.",
    markdown=True,
)
agent.print_response("O que o manual diz sobre X?", stream=True)
```

## Boas práticas

- Mantenha o `uri`/tabela do LanceDB dentro da pasta do agente (`data/lancedb`).
- Escolha `search_type` conforme o caso (hybrid é um bom padrão para estudo).
- Chunk size afeta qualidade × custo; experimente e anote resultados.
- Não recarregue a base a cada run se ela já existe; carregue uma vez.

## Exercícios sugeridos

1. Comparar respostas com e sem `search_knowledge`.
2. Testar `SearchType.vector` vs `hybrid`.
3. Adicionar um PDF e fazer perguntas específicas sobre ele.
