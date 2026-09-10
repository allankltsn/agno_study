---
name: agno-knowledge-rag
description: Conhecimento e RAG em Agno — como LLM/vetores funcionam, criar Knowledge com vector DB plugável por env (LanceDB, PgVector, S3Vectors), embeddings, adicionar documentos complexos e visualizar/medir o retrieval localmente. Use no módulo de Conhecimento e RAG.
---

# Agno Knowledge & RAG (Nível 3)

RAG (Retrieval-Augmented Generation) dá ao agente acesso a uma base de conhecimento
buscável por similaridade vetorial.

## Conceito (o que estudar)

1. Documentos são divididos em chunks.
2. Cada chunk vira um **embedding** (vetor).
3. Vetores ficam num **vector DB** (LanceDB local, PgVector, ou S3Vectors).
4. Na pergunta, busca-se os chunks mais similares e injeta-se no contexto do LLM.

## Princípio: RAG e vector DB são plugáveis por env (não mudam o domínio)

Igual ao LLM provider (`agno-providers`) e aos endpoints AWS/OTLP
(`agno-local-dev-environment`), o RAG é uma **dependência de infraestrutura**
ligada no `composition_root`. Dois eixos, ambos controlados por variável de
ambiente:

- **Ligar/desligar RAG**: `RAG_ENABLED` (true/false). Desligado, o agente roda
  sem `knowledge`; ligado, o `composition_root` monta o `Knowledge` e injeta.
- **Escolher o vector DB**: `RAG_VECTOR_DB` (`lancedb` | `pgvector` | `s3vectors`).
  Trocar o backend **não toca** em `domain/` nem `application/` (OCP + DIP).

O código do agente não muda entre local e produção — só as env vars apontam para
o backend certo. Regra de ouro: se uma env obrigatória do backend selecionado
faltar, **falhe rápido nomeando a env var** (nunca o valor), como já faz o
`config.py` do agente.

## Dependências (instale só o backend que for usar)

```bash
# Núcleo + LanceDB (default de estudo, local, sem servidor)
uv add lancedb tantivy
# Embeddings via OpenAI (ou outro embedder)
uv add openai

# PgVector (quando RAG_VECTOR_DB=pgvector)
uv add "psycopg[binary]" pgvector sqlalchemy

# S3Vectors (quando RAG_VECTOR_DB=s3vectors) — ver ressalva na seção própria
uv add boto3
```

## As três opções de vector DB (trade-offs honestos)

| Backend | Onde roda local | Onde roda em prod | Quando escolher |
|---------|-----------------|-------------------|-----------------|
| **LanceDB** | arquivo em `data/lancedb` (zero infra) | disco/EFS, embutido | estudo, protótipo, base pequena/média, sem servidor |
| **PgVector** | container Postgres no docker-compose | RDS/Aurora Postgres | já há Postgres, precisa filtrar por metadados via SQL, joins, transações |
| **S3Vectors** | **não emulado** (ver ressalva) | AWS S3 Vectors (serviço nativo) | prod serverless, custo baixo em escala, muitos vetores com acesso pouco frequente |

- **LanceDB**: o mais simples para desenvolver. Não é serviço compartilhado; não
  escala horizontal como um banco gerenciado. É o default correto do estudo.
- **PgVector**: melhor custo-benefício em produção quando você quer filtrar por
  metadados em SQL e já domina Postgres. Latência boa até alguns milhões de
  vetores com índice HNSW. Reaproveita o Postgres que o compose já sobe.
- **S3Vectors**: vetores a custo de S3, casa com arquitetura serverless/Lambda.
  **Ressalva importante abaixo** — hoje é a opção menos madura no ecossistema.

## Montar Knowledge — LanceDB (default local)

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

## Montar Knowledge — PgVector

Nativo no Agno (`from agno.vectordb.pgvector import PgVector`). A `db_url` vem por
env; a senha nunca fica no código.

```python
import os
from agno.knowledge.knowledge import Knowledge
from agno.knowledge.embedder.openai import OpenAIEmbedder
from agno.vectordb.pgvector import PgVector, SearchType

knowledge = Knowledge(
    name="Base de Estudo",
    vector_db=PgVector(
        table_name="estudo",
        db_url=os.environ["RAG_PG_DSN"],    # ex.: postgresql+psycopg://user:pass@host:5432/db
        search_type=SearchType.hybrid,      # vector | keyword | hybrid
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
)
```

> A extensão `vector` precisa existir no banco (`CREATE EXTENSION IF NOT EXISTS
> vector;`). A imagem `pgvector/pgvector` já traz a extensão; o bootstrap local
> deve garanti-la de forma idempotente.

## Montar Knowledge — S3Vectors (AWS-native, experimental)

> **Ressalva (leia antes de apostar nisso):** o **S3 Vectors** é um serviço AWS
> recente, com API própria (`s3vectors`), distinta do S3 comum. Dois pontos:
> 1. O **LocalStack não o emula** de forma confiável — desenvolvimento local do
>    S3Vectors é limitado. Na prática, você desenvolve com **LanceDB local** e usa
>    S3Vectors **só em produção**, trocando por env (`RAG_VECTOR_DB=s3vectors`).
> 2. O **suporte nativo no Agno** como classe de vector DB pode não existir/ser
>    estável na sua versão. Antes de implementar, **confirme na doc oficial** se
>    há `agno.vectordb` para S3 Vectors; se não houver, trate como *roadmap* e
>    mantenha o slot de configuração pronto, degradando com erro claro.

Padrão recomendado hoje: o `composition_root` aceita `RAG_VECTOR_DB=s3vectors`,
mas se o backend não estiver disponível na versão instalada, **falha rápido com
mensagem explicando a limitação** — em vez de fingir suporte. Assim a env fica
preparada para quando o suporte amadurecer, sem quebrar silenciosamente.

## Adicionar documentos (igual para qualquer backend)

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

## Visualizar e medir o RAG localmente

Separe duas perguntas que costumam se misturar:
**"o que o retrieval trouxe?"** (eficácia) e **"quanto custou/demorou?"** (eficiência).

### Ferramentas que o ambiente local já oferece (ver `agno-local-dev-environment`)

- **AgentOS (`localhost:7777`)** — comece por aqui. Cada run vira uma árvore de
  spans; a busca no knowledge aparece como tool call, com o que foi recuperado,
  quantos chunks e a latência daquele passo. Ideal para inspecionar caso a caso.
- **Langfuse (`localhost:3100`)** — o mais indicado para RAG: trace com spans de
  retrieval + geração, prompt/resposta, tokens e custo por chamada. É onde você vê
  "esse chunk realmente entrou no prompt e custou X tokens".
- **SigNoz / Grafana+Tempo** — latência agregada (p95/p99) do passo de retrieval ao
  longo de muitas execuções. Menos para um caso, mais para tendência.

Ordem prática: AgentOS (desenvolver) → Langfuse (tokens/custo/conteúdo) →
SigNoz/Grafana (latência agregada).

### Inspecionar o índice/embeddings

- **PgVector**: qualquer cliente SQL (DBeaver, `psql`, pgAdmin) lê a tabela,
  metadados e permite rodar a query de similaridade na mão para sanity-check.
  Grande vantagem de debugabilidade.
- **LanceDB**: abra a tabela num script/notebook Python para listar chunks. Sem UI
  dedicada tão rica quanto SQL.

### Medir eficácia (o que costuma faltar)

Latência e tokens medem **eficiência**, não se o RAG **acertou**. Para isso, um
mini conjunto de avaliação:

- Monte um **golden set**: perguntas + qual chunk/documento deveria ser recuperado.
- Meça **recall@k / hit rate** (o chunk certo apareceu no top-k?) e, opcionalmente,
  **LLM-as-judge** para a resposta final.
- Ferramentas open-source locais: **Ragas** e **DeepEval** (Python) geram
  faithfulness, context precision/recall. Isso conversa direto com a skill
  `agno-testing-strategy` (golden set + scorers).

## Boas práticas

- RAG e backend do vector DB **sempre por env** (`RAG_ENABLED`, `RAG_VECTOR_DB`);
  nunca hardcode o backend no domínio.
- Falhe rápido nomeando a env var ausente do backend selecionado; **nunca** logue
  DSN com senha nem chave de API (ver `agno-security`/`agno-secrets`).
- Mantenha o `uri`/tabela do LanceDB dentro da pasta do agente (`data/lancedb`).
- Escolha `search_type` conforme o caso (hybrid é bom padrão para estudo).
- Chunk size afeta qualidade × custo; experimente e anote resultados.
- Não recarregue a base a cada run se ela já existe; carregue uma vez.
- Ligue observabilidade de retrieval por env (ver `agno-observability`): spans de
  busca, contagem de chunks e tokens de contexto.

## Exercícios sugeridos

1. Comparar respostas com e sem `search_knowledge`.
2. Testar `SearchType.vector` vs `hybrid`.
3. Adicionar um PDF e fazer perguntas específicas sobre ele.
4. Rodar o **mesmo** golden set trocando `RAG_VECTOR_DB=lancedb` → `pgvector` e
   comparar recall@k e latência no Langfuse/SigNoz.
5. Inspecionar no Langfuse os tokens de contexto injetados por chunk size.
