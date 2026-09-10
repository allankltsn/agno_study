"""Interface HTTP — aplicação **FastAPI** que expõe o agente por HTTP.

Esta é a borda de entrada HTTP do sistema. Sua responsabilidade é *adaptar* uma
requisição HTTP ao caso de uso `AnswerQuestion`, sem conter regra de negócio:

1. **Validar a entrada na borda** com Pydantic (`ChatIn`): a pergunta com
   comprimento em ``[1, 4000]`` e o modo no conjunto finito de `Mode` do domínio.
   Corpo malformado, pergunta fora do intervalo ou modo inválido são rejeitados pelo
   FastAPI com um erro de validação de cliente (HTTP ``422``) **antes** de invocar o
   caso de uso e **sem** stacktrace nem detalhes internos (Req 14.2, 14.3).
2. **Reutilizar** `AnswerQuestion` via `composition_root.build_answer_question()` — o
   único lugar que conhece as implementações concretas (Req 14.1). Esta camada só
   enxerga o caso de uso e os DTOs (`AnswerRequest`/`AnswerResponse`/`Mode`);
   **nunca** importa tipos da Agno, que ficam confinados à `infrastructure` (Req
   14.6).
3. **Montar o caso de uso UMA vez por processo/container** (no carregamento do
   módulo, fora das rotas), de modo que a montagem — leitura de config, composição do
   `FallbackLLM`, ligação da observabilidade — seja reaproveitada entre invocações
   (importante no ambiente serverless: o objeto sobrevive entre invocações do mesmo
   contêiner Lambda).
4. **Traduzir erro de domínio (`DomainError`) em HTTP ``502``** com uma mensagem
   genérica de falha de processamento, **sem** vazar stacktrace nem detalhes internos,
   preservando o comportamento sem estado da requisição (Req 14.7).
5. **Expor** `GET /health` retornando ``200`` (Req 14.8) e a documentação OpenAPI em
   ``/docs`` (Req 14.9).

A **mesma** ``app`` FastAPI é publicada nos dois caminhos de deploy atrás do AWS API
Gateway (Mangum no caminho padrão — ver `interface/api/main.py` — e container + AWS
Lambda Web Adapter no alternativo); o código do agente não muda entre local e
produção (Req 14.4, 14.5).

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para
Python 3.9+. Sem imports de Agno (tipos da Agno ficam confinados à `infrastructure`).
"""

from __future__ import annotations

from typing import Dict, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.composition_root import build_answer_question
from suporte.domain.exceptions import DomainError

# Mensagem genérica devolvida quando um erro de domínio é traduzido em HTTP 502.
# Não contém stacktrace nem detalhes internos (Req 14.7).
_DOMAIN_ERROR_DETAIL = "falha ao processar a solicitação"

# Caso de uso montado UMA vez por processo/container, fora das rotas (Req 14.1). No
# ambiente serverless o objeto é reaproveitado entre invocações do mesmo contêiner,
# evitando remontar a config, os adapters e a observabilidade a cada requisição.
_use_case = build_answer_question()

# A mesma app FastAPI é publicada nos dois caminhos de deploy (Req 14.4, 14.5). O
# `title`/`version` alimentam a documentação OpenAPI exposta em /docs (Req 14.9).
app = FastAPI(title="Agente 01 — Suporte SLA/Fallback", version="1.0.0")


class ChatIn(BaseModel):
    """Corpo da requisição de `POST /chat` — validação na borda (Req 14.2, 14.3).

    O Pydantic rejeita corpo malformado, pergunta fora do intervalo ou modo inválido
    com HTTP ``422`` **antes** de o caso de uso ser invocado, sem vazar detalhes
    internos.

    Attributes:
        question: Pergunta do usuário; comprimento obrigatório em ``[1, 4000]``.
        mode: Modo de execução; restrito ao conjunto finito de `Mode` do domínio.
            Padrão: ``Mode.RUN`` (resposta completa).
    """

    question: str = Field(min_length=1, max_length=4000)
    mode: Mode = Mode.RUN


class ChatOut(BaseModel):
    """Corpo da resposta de `POST /chat` — espelha os campos de `AnswerResponse`.

    Além do texto, expõe os metadados de observabilidade que a `interface` renderiza
    e que permitem conferir os critérios de aceite (qual provider respondeu, custo em
    tokens, latência, se houve fallback, se estava fora de escopo e se foi encaminhado
    a um humano).

    Attributes:
        text: Texto a exibir ao usuário (resposta ou mensagem de degradação).
        provider: Nome do provider que respondeu (ex.: ``"openai"``).
        total_tokens: Total de tokens consumidos (prompt + completion).
        latency_ms: Latência da resposta em milissegundos.
        used_fallback: ``True`` quando um provider secundário respondeu.
        out_of_scope: ``True`` quando a pergunta ficou fora do escopo da documentação.
        escalated: ``True`` quando encaminhado a um humano ou em degradação graciosa.
    """

    text: str
    provider: str
    total_tokens: int
    latency_ms: float
    used_fallback: bool
    out_of_scope: bool
    escalated: bool


@app.get("/health")
def health() -> Dict[str, str]:
    """Verificação de saúde: retorna ``200`` indicando serviço operacional (Req 14.8).

    Returns:
        Um dicionário simples ``{"status": "ok"}`` (HTTP ``200`` por padrão).
    """
    return {"status": "ok"}


@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    """Responde a uma pergunta em modo *run* (resposta completa) (Req 14.2).

    A validação da entrada (comprimento 1..4000, modo no conjunto finito) já ocorreu
    no Pydantic (`ChatIn`) antes desta função ser chamada (Req 14.3). Aqui apenas
    delegamos ao caso de uso e traduzimos um eventual erro de domínio em HTTP ``502``.

    Args:
        body: Corpo já validado da requisição (pergunta e modo).

    Returns:
        Um `ChatOut` com o texto e os metadados de observabilidade.

    Raises:
        HTTPException: Com status ``502`` e mensagem genérica quando o processamento
            resulta em `DomainError`, sem stacktrace nem detalhes internos (Req 14.7).
    """
    try:
        resp: AnswerResponse = _use_case.execute(
            AnswerRequest(question=body.question, mode=body.mode)
        )
    except DomainError:
        # Erro de domínio -> 502 com mensagem genérica, sem vazar detalhes (Req 14.7).
        raise HTTPException(status_code=502, detail=_DOMAIN_ERROR_DETAIL)
    # `AnswerResponse` é uma dataclass; seus campos coincidem com os de `ChatOut`.
    return ChatOut(**resp.__dict__)


@app.post("/chat/stream")
def chat_stream(body: ChatIn) -> StreamingResponse:
    """Responde em modo *stream*, emitindo os fragmentos na ordem de geração.

    Útil no caminho de deploy em container (AWS Lambda Web Adapter), onde a resposta
    pode ser transmitida de forma incremental. A validação da entrada também ocorre no
    Pydantic (`ChatIn`) antes desta função (Req 14.3).

    Args:
        body: Corpo já validado da requisição (a pergunta é usada; o modo é ignorado,
            pois esta rota é sempre streaming).

    Returns:
        Uma `StreamingResponse` de ``text/plain`` que produz os fragmentos de texto na
        ordem em que o caso de uso os emite.
    """

    def gen() -> Iterator[str]:
        # Repassa cada fragmento na ordem produzida pelo caso de uso, sem buffering.
        yield from _use_case.execute_stream(body.question)

    return StreamingResponse(gen(), media_type="text/plain")
