"""Testes de integração/live com providers reais (Task 13.2 — Req 1.1, 2.1, 2.3).

Esta é a camada de "comportamento com provider real": diferentemente do gate de PR
(determinístico, com fakes e sem rede), aqui montamos o caso de uso **real** via
`composition_root.build_answer_question()` e exercitamos um provider de LLM concreto
(OpenAI/Groq) através da rede.

Por que ficam fora do gate de PR
--------------------------------
Estes testes:

- São **não-determinísticos** (dependem da resposta de um modelo real) e **lentos**
  (fazem chamadas de rede);
- Exigem **chaves de API reais** (`OPENAI_API_KEY`/`GROQ_API_KEY`) que não existem no
  CI padrão.

Por isso são marcados com `@pytest.mark.integration` e o gate de PR os exclui com
``pytest -m "not e2e and not system and not integration"`` (Req 17.1, 17.11). Além
disso, cada teste faz `pytest.skip` automático quando a chave exigida está ausente,
de forma que — mesmo que alguém rode ``pytest -m integration`` sem chaves — eles
**pulam** em vez de reprovar.

O que cada teste valida
-----------------------
- ``test_run_mode_returns_non_empty_answer`` — modo *run*: `AnswerQuestion.execute`
  retorna exatamente um `AnswerResponse` (via `LLMPort.ask`) com `text` não vazio para
  uma pergunta dentro do escopo da documentação injetada (Req 1.1, 2.1).
- ``test_stream_mode_yields_ordered_fragments`` — modo *stream*:
  `AnswerQuestion.execute_stream` produz uma sequência ordenada de fragmentos (via
  `LLMPort.ask_stream`) cuja concatenação forma um texto não vazio, preservando a
  ordem de emissão (Req 2.3).

Configuração de provider
-------------------------
Cada teste força uma `MODEL_PROVIDER_ORDER` de um único provider real (o que tiver
chave disponível) apenas por variável de ambiente — sem tocar em código de domínio ou
aplicação (Req 4.2). Assim o mesmo caso de uso é exercitado contra o provider real
para o qual há credencial.
"""

from __future__ import annotations

import os
from typing import List

import pytest

from suporte.application.dto import AnswerRequest, Mode
from suporte.composition_root import build_answer_question

# Todos os testes deste módulo pertencem à camada `integration` (providers reais).
pytestmark = pytest.mark.integration

# Chaves de API que habilitam cada provider real. Só verificamos a *presença* da
# env var — nunca lemos nem logamos o valor (Req 10.x).
_OPENAI_KEY_ENV = "OPENAI_API_KEY"
_GROQ_KEY_ENV = "GROQ_API_KEY"

# Pergunta dentro do escopo da documentação de estudo injetada pelo `composition_root`
# (o FAQ contém "Como redefino minha senha?"). Uma pergunta ancorada na doc reduz a
# chance de o modelo escalar por fora de escopo e mantém o teste focado nos modos.
_IN_SCOPE_QUESTION = "Como faço para redefinir a minha senha?"


def _pick_available_provider() -> str:
    """Escolhe um provider real com chave presente, ou pula o teste.

    Verifica apenas a **presença** das env vars de chave (nunca o valor). Retorna o
    nome do primeiro provider real disponível para uso em `MODEL_PROVIDER_ORDER`.

    Returns:
        O nome do provider a usar (``"openai"`` ou ``"groq"``).

    Raises:
        Skipped: Via `pytest.skip` quando nenhuma chave real está presente, de modo
            que o teste **pula** em vez de reprovar no gate de PR.
    """
    if os.environ.get(_OPENAI_KEY_ENV):
        return "openai"
    if os.environ.get(_GROQ_KEY_ENV):
        return "groq"
    pytest.skip(
        "Nenhuma chave de provider real presente "
        f"({_OPENAI_KEY_ENV}/{_GROQ_KEY_ENV}); teste de integração pulado."
    )


@pytest.fixture()
def real_provider_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configura `MODEL_PROVIDER_ORDER` para um único provider real disponível.

    Reordena os providers **apenas por variável de ambiente** (Req 4.2), sem alterar
    domínio/aplicação. Pula o teste automaticamente quando não há chave real.

    Args:
        monkeypatch: Fixture do pytest para ajustar env vars com escopo do teste.

    Returns:
        O nome do provider real selecionado.
    """
    provider = _pick_available_provider()
    monkeypatch.setenv("MODEL_PROVIDER_ORDER", provider)
    return provider


def test_run_mode_returns_non_empty_answer(real_provider_env: str) -> None:
    """Modo *run* com provider real devolve um `AnswerResponse` de texto não vazio.

    Monta o caso de uso real via `build_answer_question` e chama `execute` em
    `Mode.RUN`: deve retornar exatamente um `AnswerResponse` (via `LLMPort.ask`) com
    `text` não vazio para uma pergunta dentro do escopo da doc injetada.

    **Validates: Requirements 1.1, 2.1**
    """
    use_case = build_answer_question()

    response = use_case.execute(
        AnswerRequest(question=_IN_SCOPE_QUESTION, mode=Mode.RUN)
    )

    # Req 2.1: exatamente um AnswerResponse no modo run.
    assert response is not None
    # Req 1.1/1.2: resposta fundamentada e não vazia dentro de escopo.
    assert response.text.strip() != ""
    assert response.provider == real_provider_env


def test_stream_mode_yields_ordered_fragments(real_provider_env: str) -> None:
    """Modo *stream* com provider real emite fragmentos ordenados e não vazios.

    Monta o caso de uso real e consome `execute_stream`: deve produzir uma sequência
    ordenada de fragmentos (via `LLMPort.ask_stream`) cuja concatenação forma um texto
    não vazio, preservando a ordem de emissão.

    **Validates: Requirements 2.3**
    """
    use_case = build_answer_question()

    fragments: List[str] = list(use_case.execute_stream(_IN_SCOPE_QUESTION))

    # Req 2.3: sequência de zero ou mais fragmentos; para uma pergunta em escopo,
    # esperamos ao menos um fragmento e uma concatenação não vazia.
    assert len(fragments) >= 1
    joined = "".join(fragments)
    assert joined.strip() != ""
