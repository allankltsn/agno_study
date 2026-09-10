---
name: agno-memory-sessions
description: Memória e persistência em Agno — Db plugável por env (DynamoDB preferencial, SQLite fallback local), Sessions (histórico de conversa) e Memory factual/User Memories (fatos do usuário). Cobre os dois modos de gestão (automático update_memory_on_run vs agêntico enable_agentic_memory) e seu trade-off de custo, MemoryManager customizado, métodos de retrieval (last_n/first_n/agentic), otimização de tokens, quando o modo agêntico se justifica e backends gerenciados (Mem0/Zep). Use no módulo de Memória e Persistência.
---

# Agno Memory & Sessions (Nível 4)

Como o agente lembra: sessões (histórico da conversa) e memória (fatos aprendidos).

## Conceitos

- **Storage / Db**: onde tudo é persistido. Por preferência do projeto, o backend
  padrão é **DynamoDB**; SQLite fica como fallback local sem AWS.
- **Session**: uma conversa; guarda o histórico de mensagens e permite continuidade.
- **Memory**: fatos duráveis sobre o usuário, reutilizados entre sessões.

## Princípio: persistência plugável por env (não muda o domínio)

Igual ao LLM provider e ao RAG (ver `agno-knowledge-rag`), o `Db` é uma
**dependência de infraestrutura** ligada no `composition_root`, controlada por
variável de ambiente — `domain/`/`application/` nunca a conhecem (DIP):

- **Backend**: `AGENT_DB_BACKEND` (`dynamodb` padrão | `sqlite`).
- **Memória de usuário**: `AGENT_MEMORY_ENABLED` (sessions ficam sempre on quando
  há `Db`; a memória é opcional porque custa chamadas extras ao modelo).

O código do agente não muda entre local e produção — só as env vars apontam para o
DynamoDB do LocalStack (dev) ou da AWS real (prod).

## Backend padrão: DynamoDB

O Agno tem um `Db` nativo de DynamoDB (`from agno.db.dynamo import DynamoDb`) que
persiste sessions, memories, summaries e métricas, **criando as tabelas e índices
(GSIs) no primeiro uso**. Passe um client boto3 já apontado para o endpoint certo
(LocalStack em dev; AWS real em prod) — assim o mesmo código serve os dois.

```bash
uv add boto3   # o DynamoDb do Agno usa boto3
```

```python
import os
import boto3
from agno.db.dynamo import DynamoDb

# endpoint_url=None (env vazia) => AWS real; em dev, aponta para o LocalStack.
client = boto3.client(
    "dynamodb",
    endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
    region_name=os.getenv("AWS_REGION"),
)
db = DynamoDb(
    db_client=client,
    session_table=os.getenv("AGENT_DDB_SESSION_TABLE", "suporte-sla-sessions"),
    memory_table=os.getenv("AGENT_DDB_MEMORY_TABLE", "suporte-sla-memories"),
    region_name=os.getenv("AWS_REGION"),
)
```

> **Deixe o Agno gerenciar o schema.** Não crie as tabelas de session/memory à mão
> com um schema divergente (ex.: só `session_id` como chave) — isso quebra as
> queries do Agno, que dependem dos GSIs que ele cria. Em dev/LocalStack, o
> bootstrap apenas *diagnostica* a existência das tabelas; o Agno as cria sob
> demanda. Em produção (Lambda), a função precisa de permissão DynamoDB para
> `CreateTable`/`DescribeTable`/`UpdateTable` + leitura/escrita, escopada às
> tabelas (ver `agno-lambda-apigateway`/`agno-aws`). Se preferir criar as tabelas
> por IaC, remova as permissões de `CreateTable`/`UpdateTable`.

## TTL das tabelas de session e memory (configurado por env)

As tabelas de **session** e **memory** no DynamoDB devem ter **TTL** para expirar
dados antigos automaticamente — controla custo de armazenamento e ajuda na higiene
de dados (não guardar histórico/PII além do necessário). A janela de expiração é
**configurada por variável de ambiente**, uma por tabela, para poder variar por
ambiente (dev curto, produção mais longo):

- `AGENT_DDB_SESSION_TTL_DAYS` — expiração das sessions (ex.: `30`).
- `AGENT_DDB_MEMORY_TTL_DAYS` — expiração das memories (ex.: `180`).

Como o Agno **não** expõe TTL nativo (não há parâmetro de TTL no `DynamoDb`), o TTL
é um recurso da própria tabela DynamoDB. Duas partes, ambas necessárias:

1. **Habilitar o TTL na tabela** apontando o atributo de expiração (ex.: `expires_at`).
   Como o Agno cria as tabelas no primeiro uso, habilite o TTL **depois** que a
   tabela existir (idempotente):

   ```bash
   # em dev, via awslocal; em prod, via aws CLI / IaC. Idempotente.
   awslocal dynamodb update-time-to-live \
     --table-name "$AGENT_DDB_SESSION_TABLE" \
     --time-to-live-specification "Enabled=true,AttributeName=expires_at"
   ```

2. **Gravar o atributo de expiração** (epoch em segundos) nos itms, derivado da env.
   O DynamoDB só expira itens que tenham o atributo TTL preenchido:

   ```python
   import os, time

   def _ttl_epoch(days_env: str, default_days: int) -> int:
       days = int(os.getenv(days_env, str(default_days)))
       return int(time.time()) + days * 86_400  # epoch (segundos) de expiração
   ```

> **Cuidado (honestidade sobre o limite):** o TTL do DynamoDB só apaga itens que
> **contenham** o atributo de expiração. Se o writer do Agno não gravar esse
> atributo por conta própria, habilitar o TTL na tabela não expira nada sozinho —
> valide, na sua versão do Agno, se ele popula um campo de expiração; caso não
> popule, o TTL fica a cargo da sua camada de persistência (hook/wrapper que
> escreve `expires_at`). O deploy deve permitir `dynamodb:UpdateTimeToLive` /
> `DescribeTimeToLive` (já incluídos na policy do template SAM).

## Fallback local: SQLite

Sem AWS (ou para inspecionar rápido), use SQLite. Requer `sqlalchemy`.

```bash
uv add agno "sqlalchemy" "psycopg[binary]"   # SqliteDb usa sqlalchemy
```

```python
from agno.db.sqlite import SqliteDb

db = SqliteDb(db_file="data/agent.db")   # arquivo dentro da pasta do agente
```

## Sessões (histórico de conversa)

```python
from agno.agent import Agent
from config import get_model

agent = Agent(
    model=get_model(),
    db=db,
    add_history_to_context=True,   # injeta o histórico da sessão no contexto
    session_id="estudo-sessao-1",  # mesma sessão => continuidade
    markdown=True,
)

agent.print_response("Meu nome é Allan.")
agent.print_response("Qual é o meu nome?")  # deve lembrar dentro da sessão
```

- Mesmo `session_id` → conversa contínua.
- `session_id` diferente → conversa isolada.

## Memory factual (User Memories) — o que é e como difere de sessão

A **memória factual** (o que a doc da Agno chama de *User Memories*) é o
"ChatGPT-like memory": o agente extrai **fatos duráveis sobre o usuário** das
conversas ("moro em Curitiba", "prefiro respostas objetivas", "sou dev Python") e
os **recupera em conversas futuras** para personalizar respostas. Fica persistida
no `Db` (DynamoDB/SQLite), associada a um `user_id`, e **cruza sessões**. Não
confunda com os outros dois conceitos:

| Conceito                        | O que guarda                                        | Escopo                     |
| ------------------------------- | --------------------------------------------------- | -------------------------- |
| Session history / storage       | O que foi dito **neste thread** de conversa         | Por `session_id`           |
| **User Memories (fato)**        | Fatos duráveis do usuário (preferências, metas)     | Por `user_id`, cruza sessões |
| Session summaries               | Resumo dos pontos-chave de **uma** conversa         | Por `session_id`           |

```python
agent = Agent(
    model=get_model(),
    db=db,
    update_memory_on_run=True,     # extrai/salva fatos ao fim de cada run (ver modos)
    add_memories_to_context=True,  # injeta as memórias do usuário no contexto
    user_id="allan",               # SEMPRE passe user_id — é a chave da memória factual
    markdown=True,
)

agent.print_response("Prefiro explicações com analogias.", user_id="allan")
# Em outra sessão, o mesmo user_id ainda "lembra" a preferência.
```

> **API v2 (versão instalada):** use `update_memory_on_run=True` para ativar a
> memória factual. Em v1 os imports eram `agno.memory.v2...`; confirme a versão do
> seu projeto antes de copiar exemplos. Conteúdo alinhado à documentação oficial do
> Agno (parafraseado para conformidade de licenciamento).

## Os dois modos de gestão de memória (trade-off central de custo)

A escolha do modo é a decisão de maior impacto em custo de tokens.

### 1. Automática — `update_memory_on_run=True` (recomendada como padrão)

O `MemoryManager` roda **uma vez ao fim de cada run**, extrai fatos e salva. Custo
**previsível** (uma passada por run). É o padrão recomendado para a maioria dos casos.

### 2. Agêntica — `enable_agentic_memory=True`

O agente ganha uma tool (`update_user_memory`) e **decide durante o raciocínio**
quando criar/atualizar/apagar memórias. Mais flexível, porém **cada operação de
memória dispara uma chamada LLM aninhada** e o contexto injetado cresce conforme as
memórias acumulam — o consumo de tokens pode **escalar de forma imprevisível**.

```python
agent = Agent(
    model=get_model(),
    db=db,
    enable_agentic_memory=True,    # autonomia total sobre a memória (custa mais)
    memory_manager=memory_manager, # ver abaixo
    user_id="allan",
)
```

## MemoryManager customizado (controle de custo e qualidade)

Você controla **qual modelo** gere a memória e **quais regras** ele segue. Usar um
modelo barato só para memória é a alavanca de custo mais direta:

```python
from textwrap import dedent
from agno.memory import MemoryManager
from agno.models.openai import OpenAIChat

memory_manager = MemoryManager(
    model=OpenAIChat(id="gpt-4o-mini"),  # modelo BARATO só para gerir memória
    delete_memories=True,                # permite apagar memórias obsoletas
    additional_instructions=dedent("""
        Armazene apenas fatos duráveis e acionáveis sobre o usuário.
        NÃO armazene dados sensíveis (senhas, CPF, cartões, tokens).
        Consolide fatos redundantes em vez de duplicar.
    """),
)
```

Campos úteis (verificados na versão instalada): `model`, `additional_instructions`,
`memory_capture_instructions`, `delete_memories`, `update_memories`, `add_memories`,
`clear_memories`. As `additional_instructions` são o principal controle de qualidade
(menos ruído, sem PII).

## Recuperação (retrieval)

O `MemoryManager` suporta métodos de busca com custos diferentes
(`search_user_memories(query, limit, retrieval_method, user_id)`):

- **`last_n`** — as N memórias mais recentes (barato, determinístico).
- **`first_n`** — as N primeiras.
- **`agentic`** — busca por relevância ao contexto (exige `query`; mais cara).

```python
memories = memory_manager.search_user_memories(
    user_id="allan",
    query="preferências de estudo",
    retrieval_method="agentic",   # ou "last_n" / "first_n"
    limit=5,
)
```

Injetar tudo no contexto custa tokens: prefira `last_n` com `limit` baixo, ou
`agentic` filtrado, em vez de despejar todas as memórias no prompt.

## Otimização de custo da memória

| Estratégia               | Como                                                     | Ganho                                  |
| ------------------------ | -------------------------------------------------------- | -------------------------------------- |
| Escolha do modo          | `update_memory_on_run` em vez de `enable_agentic_memory` | Evita chamadas LLM aninhadas por op.   |
| Modelo dedicado barato   | `MemoryManager(model=gpt-4o-mini)`                       | Reduz custo da extração                |
| Limitar retrieval        | `last_n` com `limit` baixo                               | Menos tokens no contexto               |
| Filtrar por relevância   | busca `agentic`/semântica + `memory_filters`             | Só o que importa entra no prompt       |
| Poda periódica           | job que consolida/apaga antigas (`delete_memories=True`) | Contexto menor, banco enxuto           |
| Instruções de extração   | `additional_instructions` no `MemoryManager`             | Fatos melhores, sem ruído/PII          |
| Async                    | métodos assíncronos do agente/manager                    | Não bloqueia ao gravar memória         |
| Backend gerenciado       | Mem0 / Zep via toolkit                                   | Dedupe, busca vetorial e escala externa |

**Regra de ouro (custo):** o maior risco é **memória agêntica com muitas memórias
acumuladas** (cada decisão é uma chamada LLM extra + contexto crescente). A combinação
mais econômica e previsível para a maioria dos casos:

```
update_memory_on_run=True
+ MemoryManager(model="gpt-4o-mini")
+ retrieval last_n com limit baixo
+ poda periódica
```

## Quando o modo agêntico (mais caro) se justifica

O alto consumo da memória agêntica **se paga** quando o valor da autonomia supera o
gasto. Viável quando: `valor_por_usuário` × `importância_de_lembrar` >
`custo_incremental_de_tokens`.

Casos onde compensa:

1. **Relacionamento de longo prazo, alto ticket** — coaching, tutoria, consultoria
   premium; o perfil evolui e reconciliar contradições é o produto.
2. **Erro custa caro** — saúde, compliance, contas enterprise; esquecer uma
   restrição ("alérgico a X", "cláusula Y") custa mais que tokens.
3. **Fato relevante imprevisível** — pesquisa, brainstorming, copilotos; não dá para
   fixar uma regra de extração de antemão.
4. **Baixo volume, alta personalização** — assistente executivo, um cliente
   enterprise, PoCs; custo absoluto pequeno, "mágica" vende.
5. **Relações entre entidades** — quando o agente raciocina sobre conexões
   ("A depende de B, que atrasa quando C"), não só fatos isolados.
6. **Descoberta/prototipagem** — use agêntico para *aprender* quais fatos importam,
   depois destile regras e migre para automático em produção/escala.

| A favor do agêntico            | Contra (use automático)          |
| ------------------------------ | -------------------------------- |
| Poucos usuários, ticket alto   | Milhões de usuários, ticket baixo |
| Custo de erro >> custo de token | Fatos simples e previsíveis      |
| Relação de longo prazo         | Sessões pontuais/transacionais   |
| Fatos imprevisíveis/relacionais | Extração pode ser regra fixa     |
| Fase de descoberta             | Produção em escala madura        |

**Modo híbrido:** roteie por tier do usuário — agêntico para premium/complexos,
automático para o restante — decidindo no `composition_root` qual configuração de
memória injetar. Isso mantém a decisão de custo fora do domínio (DIP).

## Backends gerenciados de memória (escala e busca vetorial)

Para escala e recuperação vetorial sofisticada, o Agno integra sistemas dedicados,
que ficam **atrás de uma port** (a `application`/`domain` não os conhecem):

- **Mem0** — add/search/list/delete de memórias entre sessões.
- **Zep** — store/retrieve/search de memória por sessão de usuário.

Ganho: deduplicação, busca vetorial e escala gerenciadas fora do processo, reduzindo
o que precisa entrar no contexto.

## Tipos avançados de memória (o que estudar)

- **Session summaries**: resumir conversas longas para caber no contexto.
- **User memories**: preferências/fatos persistentes por `user_id`.
- **Isolamento por usuário/sessão**: `user_id` + `session_id` separam dados.

## Boas práticas

- Backend do `Db` **sempre por env** (`AGENT_DB_BACKEND`), com DynamoDB padrão;
  nunca hardcode o backend no domínio.
- Deixe o Agno criar/gerenciar as tabelas do DynamoDB; não imponha schema próprio.
- Configure **TTL por env** nas tabelas de session e memory
  (`AGENT_DDB_SESSION_TTL_DAYS`/`AGENT_DDB_MEMORY_TTL_DAYS`) para expirar dados
  antigos — controla custo e higiene de PII. Habilite o TTL após a tabela existir.
- Credenciais AWS vêm do ambiente (boto3); **nunca** logue chave, endpoint com
  token, nem o conteúdo de sessions/memories (pode conter PII — ver `agno-security`).
- **Sempre** forneça `user_id` na memória factual; sem ele, memórias de usuários
  diferentes se misturam no usuário "default".
- Prefira o modo **automático** (`update_memory_on_run=True`); só use
  `enable_agentic_memory` quando o custo extra se justificar (ver seção de quando o
  agêntico compensa).
- Use **modelo barato** no `MemoryManager` (ex.: `gpt-4o-mini`), sobretudo no modo
  agêntico.
- Implemente **poda/pruning** em apps de longa duração — memórias crescem
  indefinidamente e inflam contexto e custo.
- **Monitore tokens** em produção (ver `agno-observability`) para pegar picos de
  custo ligados à memória.
- **Teste com dados realistas**: 100+ memórias se comportam muito diferente de 5
  (latência de retrieval, tamanho do contexto).
- Em Lambda, prefira DynamoDB a SQLite: o disco da função é efêmero.
- Modular por ambiente no deploy: nomes de tabela por ambiente
  (`...-dev`/`-staging`/`-prod`) via `parameter_overrides` do SAM (ver
  `agno-project-architecture-solid`).
- Para estudo, inspecione a tabela DynamoDB (AWS console/`awslocal`) ou o `.db` do
  SQLite para ver o que foi persistido.
- Adicione `*.db`, `tmp/` e `data/` ao `.gitignore`.

## Exercícios sugeridos

1. Duas runs na mesma sessão vs sessões diferentes — comparar memória.
2. Ativar `update_memory_on_run=True` e verificar persistência entre sessões.
3. Rodar com `AGENT_DB_BACKEND=dynamodb` (LocalStack) e depois `sqlite`, e comparar
   o que foi persistido em cada backend.
4. Inspecionar a tabela DynamoDB no LocalStack (`awslocal dynamodb scan`) e o `.db`
   do SQLite.
5. Comparar `update_memory_on_run=True` vs `enable_agentic_memory=True` na mesma
   conversa e medir o consumo de tokens (ver `agno-observability`) para sentir o
   trade-off de custo na prática.
6. Customizar o `MemoryManager` com `additional_instructions` e um modelo barato;
   observar a diferença na qualidade e no ruído dos fatos extraídos.
