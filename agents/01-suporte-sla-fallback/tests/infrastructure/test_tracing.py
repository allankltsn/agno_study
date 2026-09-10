"""Endpoint OTLP ausente/vazio desabilita o tracing sem interromper (Task 17.5, Req 16.2).

Foco desta tarefa: quando `OTEL_EXPORTER_OTLP_ENDPOINT` está ausente/vazio — isto é,
quando `init_tracing` recebe `None`, string vazia ou apenas espaços em branco — o
sistema deve:

- **desabilitar** a exportação de tracing (não montar exporter OTLP / não tocar o
  SDK do OpenTelemetry);
- **registrar um aviso** de nível ``warning`` no log estruturado indicando que a
  exportação foi desabilitada por endpoint não configurado;
- **retornar normalmente**, sem levantar exceção, de modo que a requisição
  prossiga (Req 16.2).

Estratégia (sem rede, sem OTel/Agno reais): substituímos `_configure_agno_logging`
por um duplo que apenas registra que foi chamado, de forma que o teste exercite
**somente** o guard de endpoint ausente/vazio de `init_tracing` — sem depender do
SDK do OpenTelemetry nem do Agno. Além disso, espionamos o import preguiçoso do
`opentelemetry` para provar que, com endpoint vazio, o SDK **nunca** é acionado.
O aviso é capturado com a fixture `caplog` do pytest.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pytest

from suporte.infrastructure.observability import tracing as tracing_module
from suporte.infrastructure.observability.tracing import init_tracing


# Nome do logger local do módulo, onde os avisos de observabilidade são emitidos.
_TRACING_LOGGER_NAME = "suporte.observability.tracing"


@pytest.fixture()
def stub_agno_logging(monkeypatch: pytest.MonkeyPatch) -> List[bool]:
    """Substitui `_configure_agno_logging` por um duplo (sem Agno/SDK reais).

    O guard de endpoint ausente ainda chama `_configure_agno_logging` para manter
    o formato JSON de uma linha (Req 16.3); aqui só registramos que foi chamado,
    para não depender do Agno estar instalado.
    """
    chamado: List[bool] = []
    monkeypatch.setattr(
        tracing_module,
        "_configure_agno_logging",
        lambda *, debug_mode=False: chamado.append(debug_mode),
    )
    return chamado


class TestEndpointAusenteDesabilitaTracing:
    """Endpoint `None`/vazio/espaços desabilita a exportação sem interromper (Req 16.2)."""

    @pytest.mark.parametrize(
        "endpoint",
        [None, "", "   ", "\t\n"],
        ids=["none", "vazio", "espacos", "tabs-e-quebras"],
    )
    def test_desabilita_e_avisa_sem_levantar(
        self,
        endpoint: Optional[str],
        stub_agno_logging: List[bool],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Com endpoint ausente/vazio: registra aviso e retorna sem exceção."""
        with caplog.at_level(logging.WARNING, logger=_TRACING_LOGGER_NAME):
            # Não deve levantar: a requisição precisa prosseguir normalmente.
            result = init_tracing(endpoint)

        # `init_tracing` não retorna valor — apenas confirmamos que voltou.
        assert result is None

        # Exatamente um aviso de nível WARNING foi emitido pelo logger do módulo.
        avisos = [
            rec
            for rec in caplog.records
            if rec.name == _TRACING_LOGGER_NAME and rec.levelno == logging.WARNING
        ]
        assert len(avisos) == 1

        # A mensagem indica exportação desabilitada por endpoint não configurado.
        mensagem = avisos[0].getMessage()
        assert "desabilitado" in mensagem
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" in mensagem

        # O logger do Agno ainda é ligado (formato JSON de uma linha, Req 16.3).
        assert stub_agno_logging == [False]

    def test_nao_aciona_o_sdk_do_opentelemetry(
        self,
        stub_agno_logging: List[bool],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Com endpoint vazio, o SDK do OpenTelemetry não deve ser importado/usado.

        Fazemos o import preguiçoso de `opentelemetry` falhar de propósito: se o
        guard de endpoint ausente funcionar, esse import nunca é alcançado e o
        `ImportError` não ocorre. Assim provamos que nenhuma exportação é montada.
        """
        import builtins

        real_import = builtins.__import__

        def _explode_no_otel(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("opentelemetry"):
                raise AssertionError(
                    "SDK do OpenTelemetry nao deveria ser acionado com endpoint vazio."
                )
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _explode_no_otel)

        # Não deve levantar (nem a AssertionError acima, nem outra exceção).
        init_tracing("   ")

        # Confirma que passou pelo caminho de "tracing desabilitado".
        assert stub_agno_logging == [False]

    def test_repassa_debug_mode_ao_logger_do_agno(
        self, stub_agno_logging: List[bool], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mesmo desabilitando o tracing, o `debug_mode` é repassado ao Agno (Req 16.6/16.7)."""
        with caplog.at_level(logging.WARNING, logger=_TRACING_LOGGER_NAME):
            init_tracing(None, debug_mode=True)

        # `_configure_agno_logging` recebeu debug_mode=True.
        assert stub_agno_logging == [True]
