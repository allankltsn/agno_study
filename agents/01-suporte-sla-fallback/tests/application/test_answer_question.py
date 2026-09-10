"""Testes de unidade do caso de uso ``AnswerQuestion`` (tarefa 4.1 — testes primeiro).

Estes testes exercitam o caso de uso da camada de aplicação usando um duplo em memória
(``FakeLLM``) que implementa a ``LLMPort`` **sem acesso à rede**. A abordagem é *test
first*: os testes descrevem o contrato esperado de ``AnswerQuestion`` (a ser implementado
na tarefa 4.2) e servem como especificação executável.

O ``FakeLLM`` cobre os três comportamentos relevantes de uma port:
- retornar um ``Answer`` fixo (dentro de escopo);
- retornar um ``Answer`` com ``out_of_scope=True`` (encaminhamento a humano);
- levantar ``ProviderUnavailable`` (falha total → degradação graciosa).

Além disso, o ``FakeLLM`` **conta** quantas vezes ``ask``/``ask_stream`` foram chamados,
para permitir asserir os casos em que o caso de uso **não** deve invocar a port (pergunta
vazia/apenas espaços e modo inválido).

Contrato esperado de ``AnswerQuestion`` (do design):
- ``__init__(self, llm: LLMPort)``
- ``execute(request: AnswerRequest) -> AnswerResponse``
- ``execute_stream(question: str) -> Iterator[str]``

Requisitos cobertos: 1.3, 2.4, 3.2, 3.3, 3.4, 7.5.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

import pytest

from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer

# Importado dentro dos testes/fixtures via import direto; a classe será implementada em 4.2.
from suporte.application.use_cases.answer_question import AnswerQuestion


# --------------------------------------------------------------------------- #
# Duplo de teste: FakeLLM (implementa LLMPort sem rede)
# --------------------------------------------------------------------------- #
class FakeLLM:
    """Implementação em memória da ``LLMPort`` para testes (sem rede).

    Comportamento configurável:
    - Se ``raise_error`` for fornecido, ``ask``/``ask_stream`` levantam essa exceção
      (usado para simular ``ProviderUnavailable`` — falha total dos providers).
    - Caso contrário, ``ask`` retorna o ``answer`` fixo fornecido e ``ask_stream`` emite
      o texto desse ``answer`` em fragmentos.

    Registra ``ask_calls``/``ask_stream_calls`` e ``prompts`` para permitir asserir que o
    caso de uso **não** invoca a port em cenários de validação (pergunta vazia, modo
    inválido).
    """

    def __init__(
        self,
        answer: Optional[Answer] = None,
        *,
        raise_error: Optional[Exception] = None,
        chunks: Optional[List[str]] = None,
    ) -> None:
        self._answer = answer
        self._raise_error = raise_error
        self._chunks = chunks
        self.ask_calls = 0
        self.ask_stream_calls = 0
        self.prompts: List[str] = []

    def ask(self, prompt: str) -> Answer:
        self.ask_calls += 1
        self.prompts.append(prompt)
        if self._raise_error is not None:
            raise self._raise_error
        assert self._answer is not None, "FakeLLM sem answer nem raise_error configurado"
        return self._answer

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.ask_stream_calls += 1
        self.prompts.append(prompt)
        if self._raise_error is not None:
            raise self._raise_error
        if self._chunks is not None:
            yield from self._chunks
            return
        assert self._answer is not None, "FakeLLM sem answer nem raise_error configurado"
        # Por padrão, emite o texto do Answer fixo como um único fragmento.
        yield self._answer.text


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _in_scope_answer(text: str = "Para redefinir a senha, acesse Configurações.") -> Answer:
    return Answer(
        text=text,
        provider="openai",
        prompt_tokens=12,
        completion_tokens=8,
        latency_ms=42.0,
        out_of_scope=False,
    )


def _out_of_scope_answer() -> Answer:
    return Answer(
        text="algum texto do modelo que NAO deve vazar ao usuario",
        provider="openai",
        prompt_tokens=5,
        completion_tokens=0,
        latency_ms=15.0,
        out_of_scope=True,
    )


# --------------------------------------------------------------------------- #
# Caminho feliz (dentro de escopo) — base para os demais asserts
# --------------------------------------------------------------------------- #
def test_execute_pergunta_valida_retorna_resposta_do_modelo() -> None:
    """Pergunta válida e dentro de escopo → texto do modelo, sem escalar."""
    answer = _in_scope_answer()
    fake = FakeLLM(answer=answer)
    use_case = AnswerQuestion(llm=fake)

    resp = use_case.execute(AnswerRequest("Como redefinir a senha?", Mode.RUN))

    assert isinstance(resp, AnswerResponse)
    assert resp.text == answer.text
    assert resp.provider == "openai"
    assert resp.total_tokens == answer.total_tokens
    assert resp.latency_ms == answer.latency_ms
    assert resp.out_of_scope is False
    assert resp.escalated is False
    assert fake.ask_calls == 1


# --------------------------------------------------------------------------- #
# Requirement 1.3 — pergunta vazia / apenas espaços
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", ["", "   ", "\t", "\n", "  \t \n "])
def test_execute_pergunta_vazia_ou_espacos_retorna_validacao_sem_chamar_port(
    question: str,
) -> None:
    """Requirement 1.3: pergunta vazia/só espaços → validação, escalated=false, SEM chamar a port."""
    fake = FakeLLM(answer=_in_scope_answer())
    use_case = AnswerQuestion(llm=fake)

    resp = use_case.execute(AnswerRequest(question, Mode.RUN))

    assert isinstance(resp, AnswerResponse)
    assert resp.escalated is False
    assert resp.out_of_scope is False
    assert resp.text.strip() != ""  # mensagem de validação legível
    # NÃO deve invocar a LLMPort
    assert fake.ask_calls == 0
    assert fake.ask_stream_calls == 0


# --------------------------------------------------------------------------- #
# Requirement 3.2, 3.3, 3.4 — fora de escopo → encaminhamento a humano
# --------------------------------------------------------------------------- #
def test_execute_out_of_scope_retorna_encaminhamento_escalated_true() -> None:
    """Requirement 3.2/3.4: out_of_scope=True → mensagem de encaminhamento, out_of_scope=true, escalated=true."""
    oos = _out_of_scope_answer()
    fake = FakeLLM(answer=oos)
    use_case = AnswerQuestion(llm=fake)

    resp = use_case.execute(AnswerRequest("Qual o sentido da vida?", Mode.RUN))

    assert resp.out_of_scope is True
    assert resp.escalated is True
    # Requirement 3.4: indicação legível de transferência a atendente humano.
    assert "human" in resp.text.lower() or "atendente" in resp.text.lower()
    assert fake.ask_calls == 1


def test_execute_out_of_scope_suprime_texto_nao_fundamentado_do_modelo() -> None:
    """Requirement 3.3: o conteúdo gerado sem fundamentação NÃO deve aparecer na resposta."""
    oos = _out_of_scope_answer()
    fake = FakeLLM(answer=oos)
    use_case = AnswerQuestion(llm=fake)

    resp = use_case.execute(AnswerRequest("Pergunta fora de escopo", Mode.RUN))

    # O texto original (potencialmente alucinado) do modelo não deve vazar.
    assert oos.text not in resp.text


# --------------------------------------------------------------------------- #
# Requirement 7.5 — ProviderUnavailable → degradação graciosa
# --------------------------------------------------------------------------- #
def test_execute_provider_unavailable_degradacao_graciosa_escalated_true() -> None:
    """Requirement 7.5: todos os providers falham → mensagem amigável, escalated=true, sem exceção."""
    fake = FakeLLM(raise_error=ProviderUnavailable(attempts=["openai", "groq"]))
    use_case = AnswerQuestion(llm=fake)

    # Não deve propagar exceção ao chamador.
    resp = use_case.execute(AnswerRequest("Pergunta qualquer", Mode.RUN))

    assert isinstance(resp, AnswerResponse)
    assert resp.escalated is True
    assert resp.text.strip() != ""
    assert fake.ask_calls == 1


def test_execute_provider_unavailable_nao_vaza_stacktrace() -> None:
    """Requirement 7.5: a mensagem de degradação não deve conter stacktrace nem detalhes internos."""
    fake = FakeLLM(raise_error=ProviderUnavailable(attempts=["openai", "groq"]))
    use_case = AnswerQuestion(llm=fake)

    resp = use_case.execute(AnswerRequest("Pergunta qualquer", Mode.RUN))

    lowered = resp.text.lower()
    assert "traceback" not in lowered
    assert "providerunavailable" not in lowered
    assert "exception" not in lowered
    assert "  file " not in lowered


# --------------------------------------------------------------------------- #
# Requirement 2.4 — modo inválido rejeitado sem invocar a port
# --------------------------------------------------------------------------- #
def test_execute_modo_invalido_rejeitado_sem_invocar_port() -> None:
    """Requirement 2.4: modo não suportado → rejeição sem invocar ask/ask_stream."""
    fake = FakeLLM(answer=_in_scope_answer())
    use_case = AnswerQuestion(llm=fake)

    # Construímos um AnswerRequest com um modo inválido (não RUN nem STREAM),
    # contornando o default do dataclass para simular entrada indevida.
    bad_request = AnswerRequest("Pergunta válida", Mode.RUN)
    object.__setattr__(bad_request, "mode", "modo-invalido")

    with pytest.raises(ValueError):
        use_case.execute(bad_request)

    # A port NÃO deve ter sido invocada.
    assert fake.ask_calls == 0
    assert fake.ask_stream_calls == 0


# --------------------------------------------------------------------------- #
# execute_stream — preserva a ordem dos fragmentos (contrato de streaming)
# --------------------------------------------------------------------------- #
def test_execute_stream_preserva_ordem_dos_fragmentos() -> None:
    """execute_stream deve emitir os fragmentos na ordem produzida pela port."""
    chunks = ["Para ", "redefinir ", "a senha, ", "acesse Configurações."]
    fake = FakeLLM(answer=_in_scope_answer(), chunks=chunks)
    use_case = AnswerQuestion(llm=fake)

    emitted = list(use_case.execute_stream("Como redefinir a senha?"))

    assert emitted == chunks
    assert "".join(emitted) == "".join(chunks)
    assert fake.ask_stream_calls == 1
