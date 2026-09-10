"""Handler Lambda (Mangum) — caminho de deploy **padrão** da app FastAPI (Req 14.5).

Este módulo é o ponto de entrada do caminho de deploy **padrão**: adapta a **mesma**
``app`` FastAPI definida em `interface/api/api.py` ao modelo de invocação do AWS Lambda
atrás do API Gateway, usando o Mangum como ponte entre o evento do API Gateway e a
interface ASGI da aplicação.

Pontos importantes:

1. **Reuso da mesma app** — importamos `app` de `suporte.interface.api.api`, sem
   redefinir rotas nem validação. O código do agente é idêntico entre execução local e
   produção e entre os dois caminhos de deploy (Req 14.4, 14.5).
2. **Montagem única por contêiner** — importar `api.py` dispara, no carregamento do
   módulo, a montagem do caso de uso (`build_answer_question()`). Como o Lambda
   reaproveita o mesmo contêiner entre invocações, essa montagem — leitura de config,
   composição do `FallbackLLM`, ligação da observabilidade — é feita uma vez e
   reutilizada.
3. **Caminho alternativo** — no deploy em container (AWS Lambda Web Adapter), a mesma
   ``app`` sobe com ``uvicorn``, **sem** Mangum. Este módulo (`handler`) é usado apenas
   no caminho padrão (Req 14.5).

Compatibilidade: `from __future__ import annotations` seguindo as convenções do projeto.
"""

from __future__ import annotations

from mangum import Mangum

from suporte.interface.api.api import app

# Ponte API Gateway <-> ASGI: o `handler` é o alvo apontado pela função Lambda no
# caminho de deploy padrão (definido no template.yaml, via SAM). Cada evento do API
# Gateway é traduzido para uma requisição ASGI processada pela `app` FastAPI (Req 14.5).
handler = Mangum(app)
