"""Smoke test do sistema real — camada 3 (Task 25.1 — Req 17.13).

Este é o smoke da **camada 3** da estratégia de testes: exercita o agente
**implantado** (deploy vivo), diferentemente do gate de PR — determinístico, com fakes,
sem rede — e da camada 2 (comportamento com LLM real/Ollama). Todo o módulo é marcado
com ``@pytest.mark.system`` (via ``pytestmark``), de modo que o gate rápido o
**exclui** com ``pytest -m "not e2e and not system and not integration"`` (Req 17.1,
17.11).

O smoke tem duas frentes complementares:

1. **Contra o deploy vivo (LocalStack ou AWS via ``AGENT_BASE_URL``)** — Req 17.13:
   - ``GET /health`` → ``200`` (serviço operacional);
   - ``POST /chat`` → ``200`` **ou** ``502`` (resposta bem-sucedida **ou** erro de
     domínio traduzido), **sem depender** de resposta determinística do modelo — o
     smoke só confere que a borda respondeu com um dos dois status esperados, jamais o
     conteúdo do texto.

   Quando ``AGENT_BASE_URL`` está ausente (não há deploy disponível), estes testes se
   auto-``skip`` — nunca reprovam o gate `system` sem um alvo publicado. Espera-se que
   o setup do ambiente tenha reusado o bootstrap **idempotente** do LocalStack
   (Req 15.7, 15.8) para publicar o alvo apontado por ``AGENT_BASE_URL``; o smoke não
   provisiona recursos, apenas verifica o alvo já pronto.

2. **Handler Mangum com evento API Gateway sintético, sem AWS** — Req 14.5:
   valida a ponte API Gateway ⇄ ASGI (``handler = Mangum(app)``) invocando o
   ``handler`` diretamente com um evento REST proxy sintético para ``GET /health`` e
   asserindo ``statusCode == 200``. Como importar ``interface/api/main.py`` dispara a
   montagem do caso de uso real (``build_answer_question()`` → config/Agno), o teste
   **substitui** ``build_answer_question`` por uma fábrica de caso de uso fake **antes**
   de importar o módulo. Assim o handler é exercitado offline, sem rede nem chaves.

Reuso do bootstrap idempotente do LocalStack: o alvo em ``AGENT_BASE_URL`` deve ter
sido preparado pelo bootstrap idempotente (``localstack/init/*``); reexecutá-lo é
seguro (Req 15.8), então o setup do CI pode chamá-lo antes desta suíte sem risco de
falha por recurso já existente. Este módulo não recria recursos — mantém o smoke
barato e sem estado.
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Iterator, Optional

import pytest

import suporte.composition_root as cr
from suporte.application.dto import AnswerRequest, AnswerResponse, Mode

# Todo o módulo é camada 3 (sistema real): fora do gate de PR (Req 17.11).
pytestmark = pytest.mark.system

# Variável de ambiente que aponta para o deploy vivo (LocalStack ou AWS). Ausente →
# os testes HTTP se auto-`skip`, para nunca reprovar sem um alvo publicado.
_BASE_URL_ENV = "AGENT_BASE_URL"


# ---------------------------------------------------------------------------
# Frente 1 — Smoke contra o deploy vivo (AGENT_BASE_URL), Req 17.13
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Devolve ``AGENT_BASE_URL`` (sem barra final) ou `skip` se ausente.

    Sem alvo implantado o smoke não tem o que exercitar: `skip` mantém a suíte
    `system` verde quando não há deploy disponível, sem reprovar o pipeline.
    """
    base = os.getenv(_BASE_URL_ENV)
    if not base:
        pytest.skip(
            f"{_BASE_URL_ENV} não definido — smoke de sistema requer um deploy vivo "
            "(LocalStack ou AWS)."
        )
    return base.rstrip("/")


class TestDeployVivo:
    """`GET /health` → ``200`` e `POST /chat` → ``200`` ou ``502`` (Req 17.13)."""

    def test_health_retorna_200(self) -> None:
        """`GET /health` responde ``200`` no deploy vivo."""
        import httpx

        base = _base_url()
        resp = httpx.get(f"{base}/health", timeout=30.0)

        assert resp.status_code == 200

    def test_chat_retorna_200_ou_502_sem_depender_da_resposta_do_modelo(self) -> None:
        """`POST /chat` responde ``200`` (sucesso) ou ``502`` (erro de domínio).

        O smoke **não** afere o conteúdo da resposta do modelo — apenas que a borda
        respondeu com um dos dois status esperados, tolerando a não-determinação do
        LLM e a possível indisponibilidade de todos os providers no ambiente vivo.
        """
        import httpx

        base = _base_url()
        resp = httpx.post(
            f"{base}/chat",
            json={"question": "Como redefinir a senha?", "mode": "run"},
            timeout=60.0,
        )

        assert resp.status_code in (200, 502)


# ---------------------------------------------------------------------------
# Frente 2 — Handler Mangum com evento API Gateway sintético, sem AWS (Req 14.5)
# ---------------------------------------------------------------------------


class _FakeUseCase:
    """Caso de uso fake por trás da rota — sem rede, sem Agno, sem config real.

    Devolve um `AnswerResponse` fixo para que importar o handler Mangum não exija
    config/Agno/rede na montagem. Não é exercitado pelo evento sintético (que atinge
    ``GET /health``), mas mantém a app importável offline.
    """

    def execute(self, request: AnswerRequest) -> AnswerResponse:
        return AnswerResponse(
            text="ok",
            provider="fake",
            total_tokens=0,
            latency_ms=0.0,
            used_fallback=False,
            out_of_scope=False,
            escalated=False,
        )

    def execute_stream(self, question: str) -> Iterator[str]:
        yield ""


@pytest.fixture()
def handler(monkeypatch: pytest.MonkeyPatch):
    """Importa o `handler` Mangum com o `composition_root` neutralizado.

    Substitui `build_answer_question` por uma fábrica de `_FakeUseCase` **antes** de
    (re)importar `interface/api/api.py` (que monta o caso de uso no carregamento) e
    então `interface/api/main.py` (que define ``handler = Mangum(app)``). Recarrega os
    módulos para garantir que a montagem use o fake mesmo se outro teste já os tiver
    importado. Assim o handler é exercitado offline, sem rede nem chaves.
    """
    monkeypatch.setattr(cr, "build_answer_question", lambda: _FakeUseCase())

    import suporte.interface.api.api as api

    api = importlib.reload(api)

    import suporte.interface.api.main as main

    main = importlib.reload(main)
    return main.handler


def _synthetic_api_gateway_event(path: str, method: str) -> dict:
    """Monta um evento API Gateway REST proxy mínimo (formato v1, sem AWS).

    Cobre os campos que o Mangum lê para reconstruir a requisição ASGI: método,
    caminho, `queryStringParameters`, `headers` (com `Host`), `requestContext` e
    `body`/`isBase64Encoded`. Suficiente para uma requisição ``GET`` sem corpo.

    Args:
        path: Caminho da rota (ex.: ``"/health"``).
        method: Método HTTP (ex.: ``"GET"``).

    Returns:
        Um dicionário no formato de evento REST proxy do API Gateway.
    """
    return {
        "resource": path,
        "path": path,
        "httpMethod": method,
        "headers": {"Host": "smoke.test", "Accept": "application/json"},
        "multiValueHeaders": {},
        "queryStringParameters": None,
        "multiValueQueryStringParameters": None,
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourcePath": path,
            "httpMethod": method,
            "path": path,
            "stage": "test",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "body": None,
        "isBase64Encoded": False,
    }


class TestHandlerMangumSintetico:
    """O `handler` Mangum responde a um evento API Gateway sintético (Req 14.5)."""

    def test_handler_health_retorna_status_200(self, handler) -> None:
        """`GET /health` via evento sintético → ``statusCode == 200``, sem AWS.

        Exercita a ponte API Gateway ⇄ ASGI (Mangum) em memória: o handler traduz o
        evento para uma requisição ASGI processada pela mesma ``app`` FastAPI dos dois
        caminhos de deploy, sem rede nem contexto Lambda real.
        """
        event = _synthetic_api_gateway_event("/health", "GET")

        # O Mangum aceita `context=None`; não há dependência do runtime da AWS.
        result = handler(event, None)

        assert result["statusCode"] == 200
        # O corpo é a serialização JSON de `{"status": "ok"}` (rota GET /health).
        assert json.loads(result["body"]) == {"status": "ok"}
