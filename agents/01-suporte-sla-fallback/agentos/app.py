"""AgentOS — superfície de acompanhamento **local** do agente na porta 7777 (Req 15.4).

Este módulo apenas "veste" o `Agent` da Agno já montado pelo `composition_root`,
via `build_agent_for_agentos()`. Ele **não duplica domínio**: toda a montagem
(config tipada, ordem de providers/fallback, persona, doc do produto e ligação da
observabilidade) vive no `composition_root`/`infrastructure`; aqui só embrulhamos
o agente numa app do AgentOS e expomos a app FastAPI resultante.

Papel no arranjo (do design):
- É uma **ferramenta de desenvolvimento/acompanhamento local** (runs, sessions,
  traces) na porta 7777, servida pelo `docker-compose` com
  ``uv run uvicorn agentos.app:app`` (por isso `agentos` é um pacote importável).
- O **deploy de produção NÃO usa o AgentOS**: produção é a app FastAPI + API
  Gateway (`interface/api`). O AgentOS é conveniência de dev, não caminho de
  release.

Confinamento dos tipos da Agno (Req 12.6): o import de ``agno.os`` acontece
**apenas** aqui (na superfície do AgentOS) e na `infrastructure`. O domínio e a
aplicação nunca conhecem a Agno. Como este módulo importa a Agno no topo, ele só
é importável com o SDK instalado — o que é aceitável porque só roda no ambiente
local do Compose (nunca é importado pelo núcleo CLI nem pelos testes de unidade).

Observabilidade por env (Req 15.5, 16.1/16.2): o endpoint de tracing NÃO é
embutido no código. A ligação da observabilidade já é feita **uma única vez**
dentro de `build_agent_for_agentos()` (que chama `init_tracing(...)` lendo
``OTEL_EXPORTER_OTLP_ENDPOINT`` do ambiente); o AgentOS então exporta os traces
para o backend local (SigNoz/OTel Collector) apontado por essa env var. Endpoint
ausente/vazio desabilita o tracing sem interromper (Req 16.2).

Nota: `build_agent_for_agentos()` lê o ambiente em tempo de execução (config e
presença das env vars de chave). Isso é esperado ao subir o serviço; ao importar
o módulo o agente é montado, então ele deve ser executado com o ambiente do
`docker-compose` (ver `.env.local.example`).
"""

from __future__ import annotations

from agno.os import AgentOS

from suporte.composition_root import build_agent_for_agentos

# Reusa a montagem do composition_root — não duplica domínio (Req 15.4). O agente
# já vem com provider primário, persona, doc do produto e observabilidade ligada.
agent = build_agent_for_agentos()

# O AgentOS "veste" o agente e expõe a superfície de acompanhamento. O tracing
# OTel sai pelo endpoint lido do ambiente (OTEL_EXPORTER_OTLP_ENDPOINT), ligado
# dentro da montagem acima.
agent_os = AgentOS(agents=[agent])

# App FastAPI do AgentOS, servida pelo uvicorn na porta 7777 (docker-compose).
app = agent_os.get_app()
