"""Testes da CLI (`interface/cli/main.py`) — Task 10.2 (Req 11.1/11.2/11.3/11.6).

A CLI é a borda de entrada mais fina do sistema. Estes testes exercitam **somente** o
comportamento da borda, sem rede e sem Agno, substituindo o `build_answer_question` do
`composition_root` por um **caso de uso fake injetado** via `monkeypatch`. Assim
verificamos, de forma determinística:

1. **Validação de tamanho na borda** (Req 11.2): pergunta vazia/apenas espaços e
   pergunta com mais de 4000 caracteres são rejeitadas com mensagem de validação e
   código de saída ``2``, **sem** montar/chamar o caso de uso.
2. **Modo inválido** (Req 11.3): um `--mode` fora de `run`/`stream` é rejeitado
   listando os modos suportados, **sem** chamar o caso de uso (o `argparse` sai com
   código ``2``).
3. **Formatação da saída** (Req 11.1): no modo `run`, a saída inclui o texto e os
   metadados de observabilidade (`provider`, `total_tokens`, `latency_ms`,
   `used_fallback`).
4. **Erro de domínio** (Req 11.6): um `DomainError` que sobe até a borda vira mensagem
   amigável, **sem** stacktrace nem detalhes internos.

Detalhe de arquitetura importante para o *monkeypatch*: `run_cli` faz um import tardio
(`from suporte.composition_root import build_answer_question`) **dentro** da função.
Como esse `import ... import name` resolve o nome no módulo de origem no momento da
chamada, substituímos o atributo em `suporte.composition_root` (o módulo de origem), e
não em `suporte.interface.cli.main`.
"""

from __future__ import annotations

from typing import Iterator, List

import pytest

import suporte.composition_root as cr
from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import ProviderUnavailable
from suporte.interface.cli.main import (
    _EXIT_OK,
    _EXIT_VALIDATION,
    _MAX_QUESTION_LEN,
    run_cli,
)


class _FakeUseCase:
    """Caso de uso fake que satisfaz o contrato usado pela CLI, sem rede nem Agno.

    Registra a pergunta recebida (para asserir se o caso de uso foi ou não invocado)
    e devolve um `AnswerResponse` fixo em `execute`, ou emite fragmentos fixos em
    `execute_stream`. Alternativamente, pode ser configurado para levantar um
    `DomainError` em `execute`, exercitando o caminho de degradação da borda.
    """

    def __init__(
        self,
        response: AnswerResponse | None = None,
        raise_error: Exception | None = None,
        chunks: List[str] | None = None,
    ) -> None:
        self._response = response
        self._raise_error = raise_error
        self._chunks = chunks or []
        # Rastreamento para as asserções de "não invocou o caso de uso".
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
        yield from self._chunks


def _patch_build_answer_question(
    monkeypatch: pytest.MonkeyPatch, use_case: _FakeUseCase
) -> None:
    """Injeta o caso de uso fake no lugar de `build_answer_question`.

    Substitui o atributo no **módulo de origem** (`suporte.composition_root`), pois a
    CLI o importa tardiamente de lá dentro de `run_cli`.
    """
    monkeypatch.setattr(cr, "build_answer_question", lambda: use_case)


def _sample_response() -> AnswerResponse:
    """Um `AnswerResponse` de exemplo com metadados verificáveis."""
    return AnswerResponse(
        text="Para redefinir a senha, acesse Configurações > Segurança.",
        provider="groq",
        total_tokens=142,
        latency_ms=87.5,
        used_fallback=True,
        out_of_scope=False,
        escalated=False,
    )


class TestValidacaoDeTamanho:
    """Validação de tamanho na borda, sem invocar o caso de uso (Req 11.2)."""

    def test_pergunta_vazia_e_rejeitada_sem_chamar_o_caso_de_uso(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        exit_code = run_cli([""])

        assert exit_code == _EXIT_VALIDATION
        # Nenhuma chamada ao caso de uso (curto-circuito na borda).
        assert fake.execute_calls == []
        assert fake.stream_calls == []
        # A mensagem de validação vai para stderr.
        err = capsys.readouterr().err
        assert "pergunta" in err.lower()

    def test_pergunta_so_espacos_e_rejeitada(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        exit_code = run_cli(["     "])

        assert exit_code == _EXIT_VALIDATION
        assert fake.execute_calls == []
        assert capsys.readouterr().err.strip() != ""

    def test_pergunta_acima_de_4000_caracteres_e_rejeitada(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        pergunta_longa = "a" * (_MAX_QUESTION_LEN + 1)
        exit_code = run_cli([pergunta_longa])

        assert exit_code == _EXIT_VALIDATION
        assert fake.execute_calls == []
        err = capsys.readouterr().err
        # A mensagem informa o limite máximo de caracteres.
        assert str(_MAX_QUESTION_LEN) in err

    def test_pergunta_no_limite_de_4000_e_aceita(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exatamente 4000 caracteres é válido — o caso de uso é invocado."""
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        pergunta_no_limite = "a" * _MAX_QUESTION_LEN
        exit_code = run_cli([pergunta_no_limite])

        assert exit_code == _EXIT_OK
        assert len(fake.execute_calls) == 1


class TestModoInvalido:
    """Modo fora do conjunto finito é rejeitado listando os suportados (Req 11.3)."""

    def test_modo_invalido_e_rejeitado_sem_chamar_o_caso_de_uso(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        # `argparse` sai com SystemExit(2) para --mode fora das choices.
        with pytest.raises(SystemExit) as exc:
            run_cli(["Olá", "--mode", "turbo"])

        assert exc.value.code == _EXIT_VALIDATION
        # O caso de uso nunca foi montado/chamado.
        assert fake.execute_calls == []
        assert fake.stream_calls == []
        # A mensagem do argparse lista os modos suportados.
        err = capsys.readouterr().err
        assert "run" in err and "stream" in err

    def test_modos_suportados_sao_aceitos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanidade: `run` e `stream` são aceitos pelo parser."""
        fake_run = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake_run)
        assert run_cli(["Olá", "--mode", Mode.RUN.value]) == _EXIT_OK
        assert len(fake_run.execute_calls) == 1

        fake_stream = _FakeUseCase(chunks=["oi", " ", "mundo"])
        _patch_build_answer_question(monkeypatch, fake_stream)
        assert run_cli(["Olá", "--mode", Mode.STREAM.value]) == _EXIT_OK
        assert fake_stream.stream_calls == ["Olá"]


class TestFormatacaoDaSaida:
    """A saída inclui texto e metadados de observabilidade (Req 11.1)."""

    def test_saida_run_inclui_texto_e_metadados(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resposta = _sample_response()
        fake = _FakeUseCase(response=resposta)
        _patch_build_answer_question(monkeypatch, fake)

        exit_code = run_cli(["Como redefinir a senha?"])

        assert exit_code == _EXIT_OK
        out = capsys.readouterr().out
        # Texto da resposta presente.
        assert resposta.text in out
        # Metadados de observabilidade exigidos (Req 11.1).
        assert f"provider: {resposta.provider}" in out
        assert f"total_tokens: {resposta.total_tokens}" in out
        assert f"latency_ms: {resposta.latency_ms:.1f}" in out
        assert f"used_fallback: {resposta.used_fallback}" in out

    def test_o_caso_de_uso_recebe_a_pergunta_e_o_modo_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeUseCase(response=_sample_response())
        _patch_build_answer_question(monkeypatch, fake)

        run_cli(["Minha pergunta"])

        assert len(fake.execute_calls) == 1
        request = fake.execute_calls[0]
        assert request.question == "Minha pergunta"
        assert request.mode is Mode.RUN

    def test_saida_stream_emite_fragmentos_na_ordem(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake = _FakeUseCase(chunks=["Frag1", "Frag2", "Frag3"])
        _patch_build_answer_question(monkeypatch, fake)

        exit_code = run_cli(["Pergunta", "--mode", "stream"])

        assert exit_code == _EXIT_OK
        out = capsys.readouterr().out
        # Os fragmentos aparecem concatenados, na ordem de emissão.
        assert "Frag1Frag2Frag3" in out


class TestErroDeDominioTraduzido:
    """`DomainError` que sobe à borda vira mensagem amigável, sem stacktrace (Req 11.6)."""

    def test_domain_error_gera_mensagem_amigavel_sem_stacktrace(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # O caso de uso levanta ProviderUnavailable (subclasse de DomainError).
        erro = ProviderUnavailable(attempts=["openai", "groq"])
        fake = _FakeUseCase(raise_error=erro)
        _patch_build_answer_question(monkeypatch, fake)

        exit_code = run_cli(["Pergunta que falha"])

        # Erro de domínio tratado NÃO é falha do processo (saída 0).
        assert exit_code == _EXIT_OK
        captured = capsys.readouterr()
        # Mensagem amigável em stderr, sem stacktrace nem detalhes internos.
        assert captured.err.strip() != ""
        assert "Traceback" not in captured.err
        assert "ProviderUnavailable" not in captured.err
        # A ordem de providers tentada (detalhe interno) não vaza ao usuário.
        assert "openai" not in captured.err
        assert "groq" not in captured.err
