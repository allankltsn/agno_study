---
name: agno-memory-sessions
description: Memória e persistência em Agno — Storage (SqliteDb), Sessions (histórico de conversa) e Memory (fatos do usuário), além de tipos avançados de memória. Use no módulo de Memória e Persistência.
---

# Agno Memory & Sessions (Nível 4)

Como o agente lembra: sessões (histórico da conversa) e memória (fatos aprendidos).

## Conceitos

- **Storage / Db**: onde tudo é persistido (aqui, SQLite local).
- **Session**: uma conversa; guarda o histórico de mensagens e permite continuidade.
- **Memory**: fatos duráveis sobre o usuário, reutilizados entre sessões.

## Storage com SQLite

```bash
uv add agno   # SqliteDb já vem com agno
```

```python
from agno.db.sqlite import SqliteDb

db = SqliteDb(db_file="tmp/agent.db")
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

## Memory (fatos do usuário)

```python
agent = Agent(
    model=get_model(),
    db=db,
    enable_user_memories=True,   # o agente extrai/armazena fatos do usuário
    user_id="allan",
    markdown=True,
)

agent.print_response("Prefiro explicações com analogias.")
# Em outra sessão, o mesmo user_id ainda "lembra" a preferência.
```

## Tipos avançados de memória (o que estudar)

- **Session summaries**: resumir conversas longas para caber no contexto.
- **User memories**: preferências/fatos persistentes por `user_id`.
- **Isolamento por usuário/sessão**: `user_id` + `session_id` separam dados.

## Boas práticas

- Mantenha o arquivo SQLite dentro da pasta do agente (`tmp/agent.db`).
- Não misture `user_id` de usuários diferentes na mesma base sem intenção.
- Para estudo, inspecione a tabela SQLite para ver o que foi persistido.
- Adicione `*.db` e `tmp/` ao `.gitignore`.

## Exercícios sugeridos

1. Duas runs na mesma sessão vs sessões diferentes — comparar memória.
2. Ativar `enable_user_memories` e verificar persistência entre sessões.
3. Abrir o `.db` e inspecionar as sessões/memórias salvas.
