"""Testes do `composition_root` — montagem e ordem (Task 9.2, Req 4.2/10.6/11.5).

Foco (distinto do `test_composition_root_tracing.py`, que cobre o wiring de
observabilidade):

1. **Montagem na ordem correta** — o `FallbackLLM` recebe os adapters exatamente na
   ordem de `provider_order` (o 1º é o primário; os demais são fallback).
2. **Troca de `MODEL_PROVIDER_ORDER` reordena sem tocar `domain/`/`application/`**
   (Req 4.2) — mudar apenas a variável de ambiente reordena os providers montados,
   e o caso de uso continua vendo somente uma `LLMPort` (DIP): nem `AnswerQuestion`
   nem o domínio precisam mudar.
3. **Abort nomeando a env var ausente** (Req 10.6, 11.5) — quando a chave exigida
   por um provider falta, a montagem aborta com `RuntimeError` **nomeando a env var
   ausente**, e a mensagem **nunca** contém o valor de nenhuma chave.

Estratégia (sem rede, sem Agno real): substituímos `build_adapter` por um duplo que
apenas registra o `ProviderConfig` recebido e devolve um stub de `LLMPort` nomeado,
e `init_tracing` por um no-op. Assim exercitamos o *arranjo* (ordem + injeção) sem
construir adapters concretos nem importar o SDK. Os testes de abort usam o
`build_adapter`/`_require_api_key` reais com env controlado por `monkeypatch`.
"""

from __future__ import annotations

from typing import Iterator, List

import pytest

import suporte.composition_root as cr
from suporte.application.use_cases.answer_question import AnswerQuestion
from suporte.domain.ports import Answer, LLMPort
from suporte.infrastructure.config import ProviderConfig
from suporte.infrastructure.llm.fallback_llm import FallbackLLM


class _StubAdapter:
    """`LLMPort` de teste que só carrega um `name` para verificar a ordem.

    Não faz rede nem chama Agno: representa o adapter que `build_adapter` devolveria
    para um dado provider. O atributo `name` permite reconstruir a ordem em que os
    adapters foram compostos no `FallbackLLM`.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def ask(self, prompt: str) -> Answer:  # pragma: no cover - não exercitado
        return Answer(
            text="stub",
            provider=self.name,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:  # pragma: no cover
        yield "stub"


def _patch_build_adapter_to_stub(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Troca `build_adapter` por um duplo que registra a ordem de construção.

    Returns:
        Uma lista (mutável) que acumula, na ordem das chamadas, o `name` de cada
        `ProviderConfig` para o qual `build_adapter` foi invocado.
    """
    ordem_construida: List[str] = []

    def _fake_build_adapter(cfg: ProviderConfig) -> LLMPort:
        ordem_construida.append(cfg.name)
        return _StubAdapter(cfg.name)

    monkeypatch.setattr(cr, "build_adapter", _fake_build_adapter)
    # A observabilidade não é o foco aqui: no-op para não depender de OTel/Agno.
    monkeypatch.setattr(cr, "init_tracing", lambda *a, **k: None)
    return ordem_construida


class TestMontagemNaOrdemCorreta:
    """O `FallbackLLM` é composto na ordem de `provider_order`."""

    def test_fallback_recebe_adapters_na_ordem_de_provider_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ordem_construida = _patch_build_adapter_to_stub(monkeypatch)
        # Env controlado: ordem explícita openai -> groq.
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "openai,groq")

        use_case = cr.build_answer_question()

        # O caso de uso só enxerga uma LLMPort (DIP): internamente é o FallbackLLM.
        assert isinstance(use_case, AnswerQuestion)
        fallback = use_case._llm
        assert isinstance(fallback, FallbackLLM)

        # build_adapter foi chamado na ordem da provider_order...
        assert ordem_construida == ["openai", "groq"]
        # ...e os adapters foram compostos no FallbackLLM nessa mesma ordem.
        nomes_compostos = [p.name for p in fallback._providers]
        assert nomes_compostos == ["openai", "groq"]

    def test_primeiro_da_ordem_e_o_primario(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build_adapter_to_stub(monkeypatch)
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "groq,openai")

        use_case = cr.build_answer_question()
        fallback = use_case._llm

        # O 1º item de provider_order é o primário (índice 0 do FallbackLLM).
        assert fallback._providers[0].name == "groq"
        assert fallback._providers[1].name == "openai"


class TestTrocaDeOrdemPorEnv:
    """Mudar `MODEL_PROVIDER_ORDER` reordena sem tocar domínio/aplicação (Req 4.2)."""

    def test_trocar_env_reordena_os_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Primeira montagem: openai,groq.
        ordem_a = _patch_build_adapter_to_stub(monkeypatch)
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "openai,groq")
        fallback_a = cr.build_answer_question()._llm
        assert [p.name for p in fallback_a._providers] == ["openai", "groq"]
        assert ordem_a == ["openai", "groq"]

        # Segunda montagem: apenas a env var muda (groq,openai). Nenhuma mudança em
        # domain/ ou application/ é necessária — o mesmo composition_root reordena.
        ordem_b = _patch_build_adapter_to_stub(monkeypatch)
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "groq,openai")
        fallback_b = cr.build_answer_question()._llm
        assert [p.name for p in fallback_b._providers] == ["groq", "openai"]
        assert ordem_b == ["groq", "openai"]

    def test_caso_de_uso_so_ve_llmport_independente_da_ordem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O tipo injetado no caso de uso é sempre uma `LLMPort` (DIP).

        Prova que a troca de ordem é uma decisão de infraestrutura: `AnswerQuestion`
        não conhece providers concretos nem a ordem — recebe apenas a abstração.
        """
        _patch_build_adapter_to_stub(monkeypatch)
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "groq,openai")

        use_case = cr.build_answer_question()

        assert isinstance(use_case, AnswerQuestion)
        # O atributo injetado satisfaz o contrato LLMPort (runtime_checkable).
        assert isinstance(use_case._llm, LLMPort)


class TestAbortNomeandoEnvVarAusente:
    """Chave exigida ausente aborta nomeando a env var, sem expor valores (Req 11.5)."""

    def test_require_api_key_ausente_nomeia_a_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Garante que a env var da chave está ausente.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = ProviderConfig(
            name="openai", model_id="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )

        with pytest.raises(RuntimeError) as exc:
            cr._require_api_key(cfg)

        # A mensagem NOMEIA a env var ausente (Req 10.6, 11.5).
        assert "OPENAI_API_KEY" in str(exc.value)

    def test_require_api_key_vazia_tambem_aborta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Env var presente porém vazia é tratada como ausente.
        monkeypatch.setenv("GROQ_API_KEY", "")
        cfg = ProviderConfig(
            name="groq", model_id="llama-3.3-70b-versatile", api_key_env="GROQ_API_KEY"
        )

        with pytest.raises(RuntimeError) as exc:
            cr._require_api_key(cfg)

        assert "GROQ_API_KEY" in str(exc.value)

    def test_mensagem_de_abort_nunca_expoe_o_valor_da_chave(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mesmo com uma chave definida, o valor jamais aparece na mensagem de erro.

        (Aqui a env está definida, então `_require_api_key` passa. Verificamos que
        `build_adapter` para um provider desconhecido também não vaza valores; e,
        sobretudo, que a leitura só usa a *presença* da env — o valor nunca compõe
        a mensagem.)
        """
        segredo = "sk-super-secreto-jamais-logar"
        monkeypatch.setenv("OPENAI_API_KEY", segredo)
        cfg = ProviderConfig(
            name="openai", model_id="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )

        # Com a chave presente, _require_api_key não levanta (não expõe nada).
        cr._require_api_key(cfg)

        # E o valor da chave nunca deve aparecer numa mensagem de erro de config.
        cfg_desconhecido = ProviderConfig(
            name="provider-inexistente",
            model_id="x",
            api_key_env="OPENAI_API_KEY",
        )
        with pytest.raises(RuntimeError) as exc:
            cr.build_adapter(cfg_desconhecido)
        assert segredo not in str(exc.value)

    def test_build_answer_question_aborta_quando_chave_do_primario_falta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A montagem completa aborta nomeando a env var da chave ausente."""
        # init_tracing no-op (foco é o abort por chave ausente, não observabilidade).
        monkeypatch.setattr(cr, "init_tracing", lambda *a, **k: None)
        # Neutraliza o load_dotenv do config: sem isto, um arquivo .env real no
        # diretório do projeto repopularia OPENAI_API_KEY e mascararia o abort
        # (o teste ficaria dependente do disco do desenvolvedor).
        import suporte.infrastructure.config as config
        monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: False)
        # openai como primário, mas sem a chave definida.
        monkeypatch.setenv("MODEL_PROVIDER_ORDER", "openai,groq")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(RuntimeError) as exc:
            cr.build_answer_question()

        assert "OPENAI_API_KEY" in str(exc.value)
