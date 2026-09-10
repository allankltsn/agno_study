"""Camada 3 de testes — sistema real / deploy vivo (Req 17.1, 17.11, 17.13).

Estes testes exercitam o agente **implantado** (LocalStack ou AWS), não a app em
memória. Por dependerem de um alvo publicado e de rede, ficam **fora** do gate de PR:
são marcados com `@pytest.mark.system` e o gate rápido os exclui com
``pytest -m "not e2e and not system and not integration"``.

Sem um alvo implantado (`AGENT_BASE_URL` ausente) os testes contra HTTP se
auto-`skip`, de modo que nunca reprovam quando não há deploy disponível. O teste do
`handler` Mangum, por sua vez, roda **sem** AWS: invoca o handler com um evento API
Gateway sintético em memória.
"""
