"""Testes da app FastAPI (`interface/api/api.py`) — Task 18.3.

**Property 11: Mesma app FastAPI nos dois deploys, com validação na borda.**

A `interface/api/api.py` é a borda de entrada HTTP. Estes testes exercitam **somente**
o comportamento da borda, com `TestClient` (app ASGI em memória, **sem rede**) e uma
`LLMPort` fake por trás do caso de uso. Verificamos, de forma determinística:

1. **Validação de tamanho e de modo na borda** (Req 14.2, 14.3): `question` de
   comprimento ``0`` e ``4001`` e um `mode` inválido são rejeitados pelo Pydantic com
   HTTP ``422`` **antes** de o caso de uso ser invocado — asserimos que a `LLMPort`
   fake **não** recebeu nenhuma chamada — e **sem** stacktrace nem detalhes internos.
2. **Entrada válida** (Req 14.2): `POST /chat` com corpo válido retorna ``200`` e um
   `ChatOut` com o texto e os metadados de observabilidade esperados.
3. **`GET /health`** (Req 14.8): retorna ``200`` com corpo de saúde.
4. **Erro de domínio → `502`** (Req 14.7): quando o caso de uso resulta em
   `DomainError`, a rota traduz para HTTP ``502`` com mensagem genérica, **sem**
   stacktrace nem detalhes internos (nome da exceção, ordem de providers tentada).
5. **Documentação OpenAPI acessível** (Req 14.9): `/docs` e `/openapi.json` respondem
   ``200``.

Detalhe de arquitetura para os testes: a `api.py` monta ``_use_case =
build_answer_question()`` **no carregamento do módulo** (uma vez por processo/container,
Req 14.1). Isso exigiria config/Agno reais na importação. Para exercitar a borda sem
rede, substituímos `suporte.composition_root.build_answer_question` por uma fábrica de
um caso de uso fake **antes** de importar o módulo `api`, e então injetamos, por teste,
o caso de uso fake diretamente no atributo de módulo ``api._use_case`` (o mesmo objeto
que as rotas usam). Assim tanto a montagem quanto a execução ficam determinísticas e
offline. A **mesma** ``app`` é publicada nos dois caminhos de deploy (Mangum e
container + AWS Lambda Web Adapter), de modo que testá-la aqui cobre ambos (Req 14.5).
"""

from __future__ import annotations

import importlib
from typing import Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import suporte.composition_root as cr
from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import ProviderUnavailable


class _FakeUseCase:
    """Caso de uso fake (papel de `LLMPort` por trás da rota), sem rede nem Agno.

    Registra cada `AnswerRequest` recebido em `execute` (para asserir que a validação
    na borda **curto-circuita** antes de invocar o caso de uso) e devolve um
    `AnswerResponse` fixo — ou levanta um `DomainError` para exercitar o caminho de
    tradução para HTTP ``502``.
    """

    def __init__(
        self,
        response: Optional[AnswerResponse] = None,
        raise_error: Optional[Exception] = None,
    ) -> None:
        self._response = response
        self._raise_error = raise_error
        self.execute_calls: List[AnswerRequest] = []
        self.stream_calls: List[str] = []

    def execute(self, request: AnswerRequest) -> AnswerResponse:
        self.execute_calls.append(request)
        if self._raise_error is not None:
            raise self._raise_error
        assert self._response is not None  # configuração de teste consistente
        return self._response

    def execute_stream(self, question: str) -> Iterator[str]:
        self.stream_calls.append(question)
        yield ""


def _sample_response() -> AnswerResponse:
    """Um `AnswerResponse` de exemplo com metadados verificáveis (espelha `ChatOut`)."""
    return AnswerResponse(
        text="Para redefinir a senha, acesse Configurações > Segurança.",
        provider="groq",
        total_tokens=142,
        latency_ms=87.5,
        used_fallback=True,
        out_of_scope=False,
        escalated=False,
    )


@pytest.fixture()
def api_module(monkeypatch: pytest.MonkeyPatch):
    """Importa `interface/api/api.py` com o `composition_root` neutralizado.

    Substitui `build_answer_question` por uma fábrica que devolve um `_FakeUseCase`
    **antes** de (re)importar o módulo, de modo que a montagem no carregamento não toque
    em config/Agno/rede. Recarrega o módulo para garantir um ``_use_case`` fake mesmo se
    outro teste já o tiver importado. Devolve o módulo `api` para injeção per-teste.
    """
    monkeypatch.setattr(
        cr, "build_answer_question", lambda: _FakeUseCase(response=_sample_response())
    )
    import suporte.interface.api.api as api

    api = importlib.reload(api)
    return api


def _client_with(api_module, use_case: _FakeUseCase) -> TestClient:
    """Injeta o caso de uso fake no atributo de módulo e devolve um `TestClient`.

    As rotas leem `_use_case` do módulo em tempo de requisição; sobrescrever o atributo
    conecta a rota ao fake exato configurado por cada teste.
    """
    api_module._use_case = use_case
    return TestClient(api_module.app)


class TestValidacaoNaBorda:
    """Validação de tamanho/modo → ``422`` sem invocar o caso de uso (Req 14.2, 14.3)."""

    def test_question_vazia_retorna_422_sem_invocar_caso_de_uso(
        self, api_module
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        client = _client_with(api_module, fake)

        resp = client.post("/chat", json={"question": "", "mode": "run"})

        assert resp.status_code == 422
        # Curto-circuito na borda: o caso de uso nunca foi invocado.
        assert fake.execute_calls == []
        # Sem stacktrace nem detalhes internos no corpo de erro (Req 14.3).
        assert "Traceback" not in resp.text

    def test_question_com_4001_caracteres_retorna_422_sem_invocar_caso_de_uso(
        self, api_module
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        client = _client_with(api_module, fake)

        pergunta_longa = "a" * 4001
        resp = client.post("/chat", json={"question": pergunta_longa, "mode": "run"})

        assert resp.status_code == 422
        assert fake.execute_calls == []
        assert "Traceback" not in resp.text

    def test_mode_invalido_retorna_422_sem_invocar_caso_de_uso(
        self, api_module
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        client = _client_with(api_module, fake)

        resp = client.post("/chat", json={"question": "Olá", "mode": "turbo"})

        assert resp.status_code == 422
        assert fake.execute_calls == []
        assert "Traceback" not in resp.text

    def test_question_no_limite_de_4000_e_aceita(self, api_module) -> None:
        """Exatamente 4000 caracteres é válido — o caso de uso é invocado."""
        fake = _FakeUseCase(response=_sample_response())
        client = _client_with(api_module, fake)

        resp = client.post("/chat", json={"question": "a" * 4000, "mode": "run"})

        assert resp.status_code == 200
        assert len(fake.execute_calls) == 1


class TestEntradaValida:
    """`POST /chat` válido → ``200`` com `ChatOut` esperado (Req 14.2)."""

    def test_chat_valido_retorna_200_e_chatout_com_metadados(
        self, api_module
    ) -> None:
        resposta = _sample_response()
        fake = _FakeUseCase(response=resposta)
        client = _client_with(api_module, fake)

        resp = client.post("/chat", json={"question": "Como redefinir a senha?"})

        assert resp.status_code == 200
        body = resp.json()
        # O corpo espelha os campos de `AnswerResponse`/`ChatOut`.
        assert body["text"] == resposta.text
        assert body["provider"] == resposta.provider
        assert body["total_tokens"] == resposta.total_tokens
        assert body["latency_ms"] == resposta.latency_ms
        assert body["used_fallback"] == resposta.used_fallback
        assert body["out_of_scope"] == resposta.out_of_scope
        assert body["escalated"] == resposta.escalated
        # A pergunta e o modo default (run) chegam ao caso de uso.
        assert len(fake.execute_calls) == 1
        assert fake.execute_calls[0].question == "Como redefinir a senha?"
        assert fake.execute_calls[0].mode is Mode.RUN


class TestHealth:
    """`GET /health` → ``200`` (Req 14.8)."""

    def test_health_retorna_200(self, api_module) -> None:
        client = _client_with(api_module, _FakeUseCase(response=_sample_response()))

        resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestErroDeDominioTraduzido:
    """`DomainError` do caso de uso → ``502`` sem stacktrace nem detalhes (Req 14.7)."""

    def test_domain_error_retorna_502_sem_stacktrace_nem_detalhes(
        self, api_module
    ) -> None:
        # ProviderUnavailable é subclasse de DomainError; carrega a ordem de providers.
        erro = ProviderUnavailable(attempts=["openai", "groq"])
        fake = _FakeUseCase(raise_error=erro)
        client = _client_with(api_module, fake)

        resp = client.post("/chat", json={"question": "Pergunta que falha"})

        assert resp.status_code == 502
        # Mensagem genérica, sem stacktrace nem detalhes internos.
        assert "Traceback" not in resp.text
        assert "ProviderUnavailable" not in resp.text
        # A ordem de providers tentada (detalhe interno) não vaza ao cliente.
        assert "openai" not in resp.text
        assert "groq" not in resp.text


class TestDocumentacaoOpenAPI:
    """A documentação OpenAPI é acessível em `/docs` e `/openapi.json` (Req 14.9)."""

    def test_docs_acessivel(self, api_module) -> None:
        client = _client_with(api_module, _FakeUseCase(response=_sample_response()))

        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
