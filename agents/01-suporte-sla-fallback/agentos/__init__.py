"""Pacote `agentos` — superfície de **desenvolvimento/acompanhamento local**.

Este pacote existe apenas para o ambiente local do `docker-compose` (Req 15.4):
o serviço `agentos` roda ``uv run uvicorn agentos.app:app`` a partir de ``/app``
(o raiz do projeto montado como volume), então `agentos` precisa ser um pacote
importável pelo nome. O módulo `app` aqui dentro "veste" o agente montado pelo
`composition_root`, sem duplicar domínio.

Não faz parte do artefato de produção: o deploy serverless usa a app FastAPI +
API Gateway (`interface/api`), não o AgentOS. Por isso este pacote fica no raiz
do projeto e não é incluído no wheel (que empacota apenas `src/suporte`).
"""
