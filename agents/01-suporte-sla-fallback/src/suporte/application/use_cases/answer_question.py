"""Caso de uso ``AnswerQuestion`` — orquestração da regra de negócio.

Este é o coração da camada de aplicação. Depende **exclusivamente** da abstração
``LLMPort`` (DIP): recebe uma porta injetada no construtor e **não conhece** a existência
de fallback, timeout, retry ou de qual provider concreto respondeu. Do ponto de vista do
caso de uso, uma ``LLMPort`` pode ser um adapter simples ou o ``FallbackLLM`` (Composite) —
ele não distingue os dois (OCP).

Responsabilidades (do design):
- Validar a entrada (pergunta não vazia) e o modo solicitado — regras de *aplicação*.
- Chamar ``LLMPort.ask`` / ``LLMPort.ask_stream``.
- Traduzir o ``Answer`` do domínio em ``AnswerResponse`` (DTO de saída).
- Tratar pergunta fora de escopo (RF-03) encaminhando a um humano, sem deixar vazar o
  texto potencialmente alucinado do modelo.
- Capturar ``ProviderUnavailable`` (RNF-02) e devolver uma mensagem amigável de degradação
  graciosa — **sem** propagar exceção nem vazar stacktrace/detalhes internos.

Sobre ``used_fallback``: o ``Answer`` do domínio carrega apenas ``provider``, não a
informação de qual foi o primário. Quem conhece a ordem dos providers (e portanto se houve
fallback) é o ``FallbackLLM``, que registra/loga isso. Por isso o caso de uso reporta
``used_fallback=False`` de forma conservadora — ele não tem como inferir fallback a partir
de uma única ``Answer`` sem conhecer a ordem, o que quebraria a DIP.
"""

from __future__ import annotations

from typing import Iterator

from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import LLMPort

# Mensagens amigáveis (sem detalhes internos) usadas nos caminhos de borda.
_EMPTY_QUESTION_MESSAGE = "Envie uma pergunta válida."
_OUT_OF_SCOPE_MESSAGE = (
    "Não tenho essa informação na documentação. Vou encaminhar "
    "sua pergunta para um atendente humano."
)
_PROVIDER_UNAVAILABLE_MESSAGE = (
    "Nosso assistente está indisponível no momento. "
    "Sua pergunta será encaminhada para um atendente humano."
)


class AnswerQuestion:
    """Orquestra o atendimento de uma pergunta usando uma ``LLMPort`` injetada.

    Attributes:
        _llm: A porta de LLM (abstração). Pode ser um adapter simples ou o
            ``FallbackLLM`` — o caso de uso não faz distinção (DIP/OCP).
    """

    def __init__(self, llm: LLMPort) -> None:
        """Recebe a abstração ``LLMPort`` por injeção de dependência (DIP).

        Args:
            llm: Implementação de ``LLMPort`` (adapter concreto ou ``FallbackLLM``).
                O caso de uso não conhece nada além deste contrato.
        """
        self._llm = llm

    def execute(self, request: AnswerRequest) -> AnswerResponse:
        """Responde à pergunta em modo *run* (resposta completa).

        Passos:
        1. Rejeita modo não suportado (Req 2.4) **antes** de tocar na porta.
        2. Valida que a pergunta não é vazia/apenas espaços (Req 1.3), sem chamar a porta.
        3. Chama ``LLMPort.ask`` e traduz o ``Answer`` em ``AnswerResponse``.
        4. Se ``out_of_scope``, encaminha a humano suprimindo o texto do modelo (Req 3.x).
        5. Se ``ProviderUnavailable``, degrada graciosamente sem vazar detalhes (Req 7.5).

        Args:
            request: DTO de entrada com a pergunta e o modo desejado.

        Returns:
            Um ``AnswerResponse`` pronto para a ``interface`` renderizar.

        Raises:
            ValueError: Quando ``request.mode`` não é um modo suportado.
        """
        # Req 2.4: rejeitar modo não suportado sem invocar a porta.
        if request.mode not in (Mode.RUN, Mode.STREAM):
            raise ValueError(f"Modo não suportado: {request.mode!r}")

        # Req 1.3: pergunta vazia/apenas espaços → validação, sem chamar a porta.
        if request.question is None or request.question.strip() == "":
            return AnswerResponse(
                text=_EMPTY_QUESTION_MESSAGE,
                provider="none",
                total_tokens=0,
                latency_ms=0.0,
                used_fallback=False,
                out_of_scope=False,
                escalated=False,
            )

        try:
            answer = self._llm.ask(request.question)
        except ProviderUnavailable:
            # RNF-02 / Req 7.5: degradação graciosa — mensagem clara, sem stacktrace.
            return AnswerResponse(
                text=_PROVIDER_UNAVAILABLE_MESSAGE,
                provider="none",
                total_tokens=0,
                latency_ms=0.0,
                used_fallback=False,
                out_of_scope=False,
                escalated=True,
            )

        if answer.out_of_scope:
            # RF-03 / Req 3.2-3.4: encaminhar a humano; NÃO vazar o texto do modelo.
            return AnswerResponse(
                text=_OUT_OF_SCOPE_MESSAGE,
                provider=answer.provider,
                total_tokens=answer.total_tokens,
                latency_ms=answer.latency_ms,
                used_fallback=False,
                out_of_scope=True,
                escalated=True,
            )

        # Caminho feliz: dentro de escopo.
        return AnswerResponse(
            text=answer.text,
            provider=answer.provider,
            total_tokens=answer.total_tokens,
            latency_ms=answer.latency_ms,
            used_fallback=False,
            out_of_scope=False,
            escalated=False,
        )

    def execute_stream(self, question: str) -> Iterator[str]:
        """Responde em modo *stream*, preservando a ordem dos fragmentos.

        Delega a ``LLMPort.ask_stream`` e repassa cada fragmento na ordem em que a porta
        os produz (RF-02). Não faz buffering nem reordenação: a ordem observada pelo
        chamador é exatamente a ordem emitida pela porta.

        Args:
            question: Pergunta do usuário.

        Yields:
            Fragmentos de texto (``str``) na ordem de geração.
        """
        yield from self._llm.ask_stream(question)
