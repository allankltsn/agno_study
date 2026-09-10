---
name: agno-agentos-eval-deploy
description: Avaliacao de agentes em producao com AgentOS - transformar Agno em API FastAPI, UI web, tracing, ambientes avancados e deploy na AWS EC2. Use no modulo de Avaliacao e AgentOS.
---

# Agno AgentOS, Avaliacao e Deploy (Nivel 10)

AgentOS empacota agentes, teams e workflows em um servico web FastAPI, com UI e tracing.

## Transformar o Agno em API

```python
from agno.os import AgentOS
from agno.db.sqlite import SqliteDb
from config import get_model
from agno.agent import Agent

db = SqliteDb(db_file="tmp/agentos.db")
chat_agent = Agent(name="Chat", model=get_model(), db=db)

agent_os = AgentOS(
    id="meu-os",
    agents=[chat_agent],
    # teams=[content_team],
    # workflows=[research_pipeline],
    db=db,
    tracing=True,          # habilita tracing na UI
)
app = agent_os.get_app()   # retorna um app FastAPI

if __name__ == "__main__":
    agent_os.serve(app="main:app", reload=True)   # sobe com uvicorn
```

Dependencias:

```bash
uv add "agno[fastapi]" uvicorn
```

## UI web e tracing

1. Rode `uv run python main.py` (sobe em http://localhost:7777).
2. Abra os.agno.com, conecte o endpoint local.
3. Use Chat para testar; Sessions e Traces para inspecionar execucoes.

O tracing permite estudar, passo a passo, o que o agente fez em cada request.

## Ambientes avancados

- Registre varios agentes, teams e workflows no mesmo AgentOS.
- AgentOS descobre automaticamente componentes (models, tools, dbs) usados.
- Use um db por ambiente (dev/prod) para isolar sessoes e memoria.

## Avaliacao (o que estudar)

- Trace: verificar as tool calls, o reasoning e as saidas de cada step.
- Sessions: revisar conversas persistidas para medir qualidade e regressoes.
- Compare versoes do agente (instructions/model) usando os mesmos inputs.

## Deploy na AWS EC2

Fluxo tipico (rode manualmente no terminal, nao via agente):

1. Suba uma instancia EC2 (ex.: Amazon Linux/Ubuntu) e libere a porta 7777 no security group.
2. Instale Python 3.9+ e uv na instancia.
3. Copie o projeto (git clone ou scp) e rode `uv sync`.
4. Configure as variaveis de ambiente (chaves de API) de forma segura (nunca no codigo).
5. Rode o AgentOS atras de um process manager (systemd) e, idealmente, um proxy (Nginx) com HTTPS.
6. Aponte a UI os.agno.com para o endpoint publico da EC2.

Boas praticas de deploy:

- Nao exponha chaves; use variaveis de ambiente ou um secrets manager.
- Restrinja o security group ao minimo necessario (evite 0.0.0.0/0 aberto).
- Use HTTPS via proxy reverso; nao sirva o AgentOS cru na internet publica.
- Fixe versoes (uv.lock) para builds reprodutiveis.
- Considere autenticacao (AgentOS suporta RBAC via JWT) antes de expor multiusuario.

## Exercicios sugeridos

1. Servir um agente com AgentOS e testar pela UI.
2. Registrar um team e um workflow no mesmo AgentOS.
3. Inspecionar um trace e uma sessao persistida.
4. Fazer um deploy de teste em uma EC2 pequena e acessar pela UI.
