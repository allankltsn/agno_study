"""Testes de unidade do log estruturado (infrastructure/observability/logging.py).

Cobrem o contrato de observabilidade por requisição (Req 9) e as garantias de
privacidade/segredos (Req 10.5). A estratégia é capturar a **saída real** emitida
por `log_request` — uma única linha de JSON — e verificar sobre o JSON já parseado:

- **Exatamente um registro** por chamada (Req 9.1).
- **Presença de todos os campos** exigidos: `question_hash`, `provider`,
  `latency_ms`, tokens (`prompt_tokens`/`completion_tokens`/`total_tokens`),
  `used_fallback`, `attempts`, `result` (Req 9.1).
- **Tipos corretos**: `latency_ms >= 0`, tokens `>= 0`, `attempts` é lista,
  `used_fallback` é booleano (Req 9.2).
- **Privacidade**: o texto em claro da pergunta **não** aparece na linha; apenas
  seu `question_hash` determinístico (Req 9.5, 10.5).
- **Segredos**: nenhuma chave de API aparece na linha (Req 10.5).

Para capturar a saída sem depender de configuração global de logging, injeta-se um
`logging.Logger` dedicado com um handler apontando para um `StringIO`. Assim o
teste é isolado, determinístico e não escreve em `stdout`.
"""

import io
import json
import logging
from typing import Any, Dict, List, Tuple

import pytest

from suporte.infrastructure.observability.logging import (
    StructuredLogger,
    hash_question,
    log_request,
)


def _make_capturing_logger() -> Tuple[logging.Logger, io.StringIO]:
    """Cria um `logging.Logger` isolado que escreve apenas a mensagem num buffer.

    Retorna a tupla `(logger, buffer)`. O formatter usa somente `%(message)s`
    para que o buffer contenha exatamente a(s) linha(s) JSON emitida(s), sem
    prefixos de nível/tempo que quebrariam o parsing.
    """
    buffer = io.StringIO()
    handler = logging.StreamHandler(stream=buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Nome único por buffer evita reaproveitar handlers entre testes (getLogger
    # é um singleton por nome). O id do buffer é estável durante o teste.
    logger = logging.getLogger("test.suporte.request.{}".format(id(buffer)))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, buffer


def _emitted_lines(buffer: io.StringIO) -> List[str]:
    """Divide o conteúdo do buffer em linhas não vazias."""
    return [line for line in buffer.getvalue().splitlines() if line.strip()]


# Valores base de uma chamada bem-sucedida típica. A pergunta em claro é mantida
# separada para conferir que ela nunca vaza — só entra como `question_hash`.
PLAINTEXT_QUESTION = "Como eu troco a bateria do produto X?"
FAKE_API_KEY = "sk-test-SUPERSECRETKEY1234567890"


def _base_kwargs(**overrides: Any) -> Dict[str, Any]:
    """Monta os kwargs de `log_request` com defaults sobrescrevíveis."""
    kwargs: Dict[str, Any] = dict(
        question_hash=hash_question(PLAINTEXT_QUESTION),
        provider="openai",
        latency_ms=123.4,
        prompt_tokens=10,
        completion_tokens=7,
        used_fallback=False,
        attempts=["openai"],
        result="ok",
    )
    kwargs.update(overrides)
    return kwargs


class TestExatamenteUmRegistro:
    """Cada chamada a `log_request` emite exatamente uma linha (Req 9.1)."""

    def test_uma_chamada_emite_uma_linha(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        lines = _emitted_lines(buffer)
        assert len(lines) == 1

    def test_duas_chamadas_emitem_duas_linhas(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        log_request(logger=logger, **_base_kwargs(result="error"))
        lines = _emitted_lines(buffer)
        assert len(lines) == 2

    def test_linha_e_json_valido(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        (line,) = _emitted_lines(buffer)
        # Não deve levantar: a linha precisa ser JSON parseável.
        json.loads(line)


class TestPresencaDeCampos:
    """Todos os campos exigidos pelo Req 9.1 devem estar presentes no registro."""

    def test_todos_os_campos_presentes(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        (line,) = _emitted_lines(buffer)
        record = json.loads(line)

        for field in (
            "question_hash",
            "provider",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "used_fallback",
            "attempts",
            "result",
        ):
            assert field in record, "campo ausente no registro: {}".format(field)

    def test_total_tokens_e_derivado_da_soma(self) -> None:
        # total_tokens nunca é recebido pronto: é prompt + completion (Req 8.3).
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(prompt_tokens=10, completion_tokens=7))
        (line,) = _emitted_lines(buffer)
        record = json.loads(line)
        assert record["total_tokens"] == 17

    def test_valores_refletem_a_chamada(self) -> None:
        logger, buffer = _make_capturing_logger()
        kwargs = _base_kwargs(
            provider="groq",
            used_fallback=True,
            attempts=["openai", "groq"],
            result="ok",
        )
        log_request(logger=logger, **kwargs)
        (line,) = _emitted_lines(buffer)
        record = json.loads(line)
        assert record["provider"] == "groq"
        assert record["used_fallback"] is True
        assert record["attempts"] == ["openai", "groq"]
        assert record["result"] == "ok"


class TestTiposCorretos:
    """Tipos e faixas dos campos (Req 9.2)."""

    def test_latency_ms_nao_negativa(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(latency_ms=0.0))
        record = json.loads(_emitted_lines(buffer)[0])
        assert isinstance(record["latency_ms"], (int, float))
        assert record["latency_ms"] >= 0

    def test_tokens_nao_negativos(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(prompt_tokens=0, completion_tokens=0))
        record = json.loads(_emitted_lines(buffer)[0])
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            assert isinstance(record[field], int)
            assert record[field] >= 0

    def test_attempts_e_lista(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(attempts=["openai", "groq"]))
        record = json.loads(_emitted_lines(buffer)[0])
        assert isinstance(record["attempts"], list)
        assert record["attempts"] == ["openai", "groq"]

    def test_attempts_normaliza_iteravel_para_lista(self) -> None:
        # A interface aceita qualquer iterável; o JSON precisa de uma lista.
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(attempts=iter(["openai", "groq"])))
        record = json.loads(_emitted_lines(buffer)[0])
        assert record["attempts"] == ["openai", "groq"]

    def test_used_fallback_e_booleano(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(used_fallback=True))
        record = json.loads(_emitted_lines(buffer)[0])
        assert isinstance(record["used_fallback"], bool)

    def test_result_invalido_levanta_valueerror(self) -> None:
        logger, _ = _make_capturing_logger()
        with pytest.raises(ValueError):
            log_request(logger=logger, **_base_kwargs(result="talvez"))


class TestPrivacidadeEsegredos:
    """A pergunta em claro e chaves de API nunca aparecem no log (Req 9.5, 10.5)."""

    def test_pergunta_em_claro_nao_aparece(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        raw = buffer.getvalue()
        # O texto literal da pergunta não deve estar na saída, em nenhuma parte.
        assert PLAINTEXT_QUESTION not in raw
        # Nem fragmentos distintivos da pergunta.
        assert "bateria" not in raw

    def test_apenas_o_hash_da_pergunta_aparece(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        record = json.loads(_emitted_lines(buffer)[0])
        assert record["question_hash"] == hash_question(PLAINTEXT_QUESTION)
        # Hash SHA-256 em hex: 64 caracteres.
        assert len(record["question_hash"]) == 64

    def test_nenhuma_chave_de_api_e_logada(self) -> None:
        # A interface não tem por onde receber uma chave; ainda assim, garantimos
        # que uma chave conhecida não aparece na saída em nenhuma circunstância.
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        raw = buffer.getvalue()
        assert FAKE_API_KEY not in raw
        assert "sk-" not in raw
        assert "api_key" not in raw.lower()


class TestRequestIdNoLog:
    """O `request_id` entra como campo quando propagado (Req 16.4) e sem PII (Req 16.5)."""

    def test_request_id_aparece_como_campo_quando_informado(self) -> None:
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(request_id="abc123def456"))
        record = json.loads(_emitted_lines(buffer)[0])
        assert record["request_id"] == "abc123def456"

    def test_request_id_omitido_quando_ausente(self) -> None:
        # Compatibilidade: chamadas que não propagam request_id não ganham o campo.
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs())
        record = json.loads(_emitted_lines(buffer)[0])
        assert "request_id" not in record

    def test_request_id_nao_traz_pergunta_nem_chave(self) -> None:
        # O request_id é um id opaco: mesmo com ele, a pergunta em claro e a chave
        # continuam ausentes; a pergunta só aparece como question_hash (Req 16.5).
        logger, buffer = _make_capturing_logger()
        log_request(logger=logger, **_base_kwargs(request_id="deadbeef"))
        raw = buffer.getvalue()
        assert PLAINTEXT_QUESTION not in raw
        assert "bateria" not in raw
        assert FAKE_API_KEY not in raw

    def test_structured_logger_propaga_request_id(self) -> None:
        logger, buffer = _make_capturing_logger()
        structured = StructuredLogger(logger=logger)
        kwargs = _base_kwargs(request_id="req-42")
        kwargs.pop("logger", None)
        structured.log_request(**kwargs)
        record = json.loads(_emitted_lines(buffer)[0])
        assert record["request_id"] == "req-42"


class TestStructuredLoggerDelegacao:
    """`StructuredLogger` delega para `log_request` mantendo as mesmas garantias."""

    def test_delegacao_emite_um_registro_completo(self) -> None:
        logger, buffer = _make_capturing_logger()
        structured = StructuredLogger(logger=logger)
        kwargs = _base_kwargs()
        kwargs.pop("logger", None)  # StructuredLogger não expõe `logger`.
        structured.log_request(**kwargs)

        lines = _emitted_lines(buffer)
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["question_hash"] == hash_question(PLAINTEXT_QUESTION)
        assert record["result"] == "ok"
        # A pergunta em claro segue ausente pela via da classe.
        assert PLAINTEXT_QUESTION not in buffer.getvalue()
