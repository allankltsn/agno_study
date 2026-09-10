"""Testes do wiring de observabilidade no `composition_root` (Task 17.3, Req 16.8).

Foco: garantir que `build_answer_question` (e `build_agent_for_agentos`) ligam a
observabilidade uma vez, chamando `init_tracing(settings.otel_endpoint,
debug_mode=settings.debug_mode)` ao montar o agente — e que essa configuração vive
apenas no `composition_root`/`infrastructure`, nunca no domínio/aplicação (Req 16.8).

Estratégia (sem rede, sem Agno real): substituímos `load_settings`,
`_build_fallback` e `init_tracing` por duplos, de modo a exercitar apenas o
*arranjo* do wiring — não os providers concretos nem o SDK de tracing.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pytest

import suporte.composition_root as cr
from suporte.application.use_cases.answer_question import AnswerQuestion
from suporte.infrastructure.config import Settings, default_provider_registry


def _fake_settings(
    *, otel_endpoint: Optional[str], debug_mode: bool
) -> Settings:
    """Monta um `Settings` mínimo para os testes de wiring."""
    return Settings(
        provider_order=["openai"],
        providers=default_provider_registry(),
        otel_endpoint=otel_endpoint,
        debug_mode=debug_mode,
    )


class TestBuildAnswerQuestionLigaTracing:
    """`build_answer_question` liga `init_tracing` com os valores de `Settings`."""

    def test_chama_init_tracing_com_endpoint_e_debug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: List[Tuple[Optional[str], bool]] = []

        def _fake_init_tracing(
            endpoint: Optional[str], *, debug_mode: bool = False
        ) -> None:
            calls.append((endpoint, debug_mode))

        settings = _fake_settings(
            otel_endpoint="http://localhost:4317", debug_mode=True
        )
        monkeypatch.setattr(cr, "load_settings", lambda: settings)
        monkeypatch.setattr(cr, "init_tracing", _fake_init_tracing)
        # Duplo do fallback: o wiring do tracing não depende do LLM concreto.
        monkeypatch.setattr(cr, "_build_fallback", lambda s: object())

        use_case = cr.build_answer_question()

        assert isinstance(use_case, AnswerQuestion)
        # init_tracing foi chamado exatamente uma vez, com endpoint + debug de Settings.
        assert calls == [("http://localhost:4317", True)]

    def test_repassa_endpoint_none_quando_desabilitado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: List[Tuple[Optional[str], bool]] = []

        def _fake_init_tracing(
            endpoint: Optional[str], *, debug_mode: bool = False
        ) -> None:
            calls.append((endpoint, debug_mode))

        settings = _fake_settings(otel_endpoint=None, debug_mode=False)
        monkeypatch.setattr(cr, "load_settings", lambda: settings)
        monkeypatch.setattr(cr, "init_tracing", _fake_init_tracing)
        monkeypatch.setattr(cr, "_build_fallback", lambda s: object())

        cr.build_answer_question()

        # Endpoint None é repassado (init_tracing desabilita o tracing sem interromper).
        assert calls == [(None, False)]

    def test_tracing_ligado_antes_de_montar_o_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A observabilidade é ligada antes de compor o LLM (ordem do wiring)."""
        eventos: List[str] = []

        def _fake_init_tracing(
            endpoint: Optional[str], *, debug_mode: bool = False
        ) -> None:
            eventos.append("init_tracing")

        def _fake_build_fallback(s: Any) -> object:
            eventos.append("build_fallback")
            return object()

        settings = _fake_settings(otel_endpoint="x", debug_mode=False)
        monkeypatch.setattr(cr, "load_settings", lambda: settings)
        monkeypatch.setattr(cr, "init_tracing", _fake_init_tracing)
        monkeypatch.setattr(cr, "_build_fallback", _fake_build_fallback)

        cr.build_answer_question()

        assert eventos == ["init_tracing", "build_fallback"]
