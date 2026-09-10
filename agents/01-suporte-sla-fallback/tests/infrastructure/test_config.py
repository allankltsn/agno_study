"""Testes de unidade da configuração tipada (infrastructure/config.py).

Foco desta tarefa (5.2): validar a tradução de variáveis de ambiente em `Settings`.

- Parsing de `MODEL_PROVIDER_ORDER` como lista separada por vírgulas (Req 4.1).
- Defaults aplicados quando as env vars estão ausentes/vazias (Req 4.1, 6.4).
- Cada ramo de `ValueError` de `load_settings`:
    - provider desconhecido (Req 4.5),
    - ordem resolvida vazia (Req 4.6),
    - `total_timeout_s < per_attempt_timeout_s` (Req 6.4).
- `ProviderConfig` guarda apenas o **nome** da env var da chave, nunca o valor (Req 10.2).

Nota sobre isolamento: `load_settings` chama `load_dotenv(override=False)`, que pode ler
um arquivo `.env` real do diretório do projeto. Para tornar os testes determinísticos, a
fixture `clean_env` remove (via `monkeypatch.delenv`) todas as variáveis relevantes antes
de cada teste; os testes que precisam de um valor específico o definem explicitamente com
`monkeypatch.setenv`. Como `monkeypatch` desfaz as alterações ao final de cada teste, não
há vazamento de estado entre casos.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.infrastructure.config import (
    DEFAULT_BACKOFF_BASE_S,
    DEFAULT_MAX_ATTEMPTS_PER_PROVIDER,
    DEFAULT_PER_ATTEMPT_TIMEOUT_S,
    DEFAULT_TOTAL_TIMEOUT_S,
    ENV_APP_ENV,
    ENV_BACKOFF_BASE,
    ENV_MAX_ATTEMPTS,
    ENV_OTEL_ENDPOINT,
    ENV_PER_ATTEMPT_TIMEOUT,
    ENV_PROVIDER_ORDER,
    ENV_TOTAL_TIMEOUT,
    ProviderConfig,
    Settings,
    default_provider_registry,
    load_settings,
)

# Env vars que influenciam load_settings; limpas antes de cada teste para isolar de um
# eventual arquivo .env presente no diretório do projeto.
_CONFIG_ENV_VARS = (
    ENV_PROVIDER_ORDER,
    ENV_PER_ATTEMPT_TIMEOUT,
    ENV_TOTAL_TIMEOUT,
    ENV_MAX_ATTEMPTS,
    ENV_BACKOFF_BASE,
    ENV_OTEL_ENDPOINT,
    ENV_APP_ENV,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove todas as env vars de configuração e neutraliza `load_dotenv`.

    Sem isso, um `.env` no diretório do projeto poderia definir, por exemplo,
    `MODEL_PROVIDER_ORDER` e tornar os testes não determinísticos.
    """
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Neutraliza a leitura de um `.env` real durante os testes: no-op determinístico.
    monkeypatch.setattr(
        "suporte.infrastructure.config.load_dotenv", lambda *a, **k: False
    )


class TestParseProviderOrder:
    """Parsing de `MODEL_PROVIDER_ORDER` como lista separada por vírgulas (Req 4.1)."""

    def test_ordem_simples(self, clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "openai,groq")
        settings = load_settings()
        assert settings.provider_order == ["openai", "groq"]

    def test_ordem_invertida_e_preservada(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "groq,openai")
        settings = load_settings()
        assert settings.provider_order == ["groq", "openai"]

    def test_um_unico_provider(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "openai")
        settings = load_settings()
        assert settings.provider_order == ["openai"]

    def test_espacos_ao_redor_sao_removidos(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "  openai , groq  ")
        settings = load_settings()
        assert settings.provider_order == ["openai", "groq"]

    def test_itens_vazios_sao_descartados(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Vírgula sobrando e itens vazios entre vírgulas são tolerados.
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "openai,,groq,")
        settings = load_settings()
        assert settings.provider_order == ["openai", "groq"]


class TestDefaultsAplicados:
    """Defaults aplicados quando as env vars estão ausentes/vazias (Req 4.1, 6.x)."""

    def test_provider_order_padrao_quando_ausente(self, clean_env: None) -> None:
        # Sem MODEL_PROVIDER_ORDER, usa DEFAULT_PROVIDER_ORDER = "openai,groq".
        settings = load_settings()
        assert settings.provider_order == ["openai", "groq"]
        # A ordem padrão contém ao menos um provider (Req 4.1).
        assert len(settings.provider_order) >= 1

    def test_provider_order_padrao_quando_vazia(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # String vazia/em branco cai no default, não vira lista vazia.
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "   ")
        settings = load_settings()
        assert settings.provider_order == ["openai", "groq"]

    def test_timeouts_e_tentativas_padrao(self, clean_env: None) -> None:
        settings = load_settings()
        assert settings.per_attempt_timeout_s == DEFAULT_PER_ATTEMPT_TIMEOUT_S
        assert settings.total_timeout_s == DEFAULT_TOTAL_TIMEOUT_S
        assert settings.max_attempts_per_provider == DEFAULT_MAX_ATTEMPTS_PER_PROVIDER
        assert settings.backoff_base_s == DEFAULT_BACKOFF_BASE_S

    def test_valores_de_env_sobrescrevem_defaults(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PER_ATTEMPT_TIMEOUT, "5.5")
        monkeypatch.setenv(ENV_TOTAL_TIMEOUT, "30")
        monkeypatch.setenv(ENV_MAX_ATTEMPTS, "3")
        monkeypatch.setenv(ENV_BACKOFF_BASE, "1.25")
        settings = load_settings()
        assert settings.per_attempt_timeout_s == 5.5
        assert settings.total_timeout_s == 30.0
        assert settings.max_attempts_per_provider == 3
        assert settings.backoff_base_s == 1.25

    def test_env_numerica_vazia_cai_no_default(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PER_ATTEMPT_TIMEOUT, "")
        monkeypatch.setenv(ENV_TOTAL_TIMEOUT, "   ")
        settings = load_settings()
        assert settings.per_attempt_timeout_s == DEFAULT_PER_ATTEMPT_TIMEOUT_S
        assert settings.total_timeout_s == DEFAULT_TOTAL_TIMEOUT_S

    def test_settings_e_imutavel(self, clean_env: None) -> None:
        settings = load_settings()
        with pytest.raises(Exception):
            settings.per_attempt_timeout_s = 1.0  # type: ignore[misc]


class TestObservabilidadeConfig:
    """`otel_endpoint` e `debug_mode` derivados do ambiente (Req 16.1/16.6/16.7).

    - `OTEL_EXPORTER_OTLP_ENDPOINT` ausente/vazio => `otel_endpoint is None`
      (o composition_root desabilita o tracing com aviso, Req 16.2).
    - `APP_ENV` só liga `debug_mode` em dev; desligado por padrão fora de dev.
    """

    def test_otel_endpoint_none_quando_ausente(self, clean_env: None) -> None:
        settings = load_settings()
        assert settings.otel_endpoint is None

    def test_otel_endpoint_none_quando_vazio(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_OTEL_ENDPOINT, "   ")
        settings = load_settings()
        assert settings.otel_endpoint is None

    def test_otel_endpoint_lido_e_normalizado(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_OTEL_ENDPOINT, "  http://localhost:4317  ")
        settings = load_settings()
        assert settings.otel_endpoint == "http://localhost:4317"

    def test_debug_mode_desligado_por_padrao(self, clean_env: None) -> None:
        # APP_ENV ausente => debug desligado (Req 16.7).
        settings = load_settings()
        assert settings.debug_mode is False

    def test_debug_mode_desligado_fora_de_dev(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_APP_ENV, "production")
        settings = load_settings()
        assert settings.debug_mode is False

    def test_debug_mode_ligado_em_dev(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Aceita variações de caixa e os aliases de desenvolvimento (Req 16.6).
        for valor in ("dev", "development", "LOCAL", "Development"):
            monkeypatch.setenv(ENV_APP_ENV, valor)
            settings = load_settings()
            assert settings.debug_mode is True, valor


class TestValueErrorProviderDesconhecido:
    """Provider desconhecido na ordem → `ValueError` nomeando o provider (Req 4.5)."""

    def test_provider_desconhecido_levanta_value_error(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "openai,inexistente")
        with pytest.raises(ValueError) as excinfo:
            load_settings()
        # A mensagem deve nomear o provider desconhecido (Req 4.5).
        assert "inexistente" in str(excinfo.value)

    def test_provider_desconhecido_isolado(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, "azure")
        with pytest.raises(ValueError) as excinfo:
            load_settings()
        assert "azure" in str(excinfo.value)


class TestValueErrorOrdemVazia:
    """Ordem resolvida vazia → `ValueError` exigindo ao menos um provider (Req 4.6).

    Como uma env var ausente/em branco cai no default, para forçar a lista vazia usamos
    uma string composta apenas de vírgulas (todos os itens são descartados no parsing).
    """

    def test_ordem_apenas_virgulas_levanta_value_error(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, ",,,")
        with pytest.raises(ValueError) as excinfo:
            load_settings()
        # Mensagem indica que ao menos um provider é obrigatório (Req 4.6).
        assert ENV_PROVIDER_ORDER in str(excinfo.value)

    def test_ordem_virgulas_e_espacos_levanta_value_error(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PROVIDER_ORDER, " , , ")
        with pytest.raises(ValueError):
            load_settings()


class TestValueErrorTotalMenorQuePerAttempt:
    """`total_timeout_s < per_attempt_timeout_s` → `ValueError` (Req 6.4)."""

    def test_total_menor_que_per_attempt_levanta_value_error(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_PER_ATTEMPT_TIMEOUT, "10")
        monkeypatch.setenv(ENV_TOTAL_TIMEOUT, "5")
        with pytest.raises(ValueError) as excinfo:
            load_settings()
        msg = str(excinfo.value)
        assert "total_timeout_s" in msg
        assert "per_attempt_timeout_s" in msg

    def test_total_igual_a_per_attempt_e_valido(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A restrição é total >= per_attempt; igualdade é permitida (Req 6.4).
        monkeypatch.setenv(ENV_PER_ATTEMPT_TIMEOUT, "10")
        monkeypatch.setenv(ENV_TOTAL_TIMEOUT, "10")
        settings = load_settings()
        assert settings.total_timeout_s == 10.0
        assert settings.per_attempt_timeout_s == 10.0


class TestProviderConfigGuardaSomenteNomeDaEnvVar:
    """`ProviderConfig` guarda apenas o **nome** da env var da chave (Req 10.2)."""

    def test_api_key_env_e_nome_nao_valor(self) -> None:
        cfg = ProviderConfig(
            name="openai", model_id="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )
        # Guarda o NOME da variável de ambiente, não o valor da chave.
        assert cfg.api_key_env == "OPENAI_API_KEY"

    def test_registro_padrao_guarda_nomes_de_env_var(self) -> None:
        registry = default_provider_registry()
        assert registry["openai"].api_key_env == "OPENAI_API_KEY"
        assert registry["groq"].api_key_env == "GROQ_API_KEY"

    def test_api_key_env_termina_com_sufixo_de_nome_de_env(self) -> None:
        # Convenção de nome de env var (maiúsculas + "_API_KEY"): sinal de que é um NOME.
        registry = default_provider_registry()
        for cfg in registry.values():
            assert cfg.api_key_env.isupper()
            assert cfg.api_key_env.endswith("_API_KEY")

    def test_provider_config_e_imutavel(self) -> None:
        cfg = ProviderConfig(
            name="openai", model_id="gpt-4o-mini", api_key_env="OPENAI_API_KEY"
        )
        with pytest.raises(Exception):
            cfg.api_key_env = "chave-real-vazada"  # type: ignore[misc]

    def test_registro_padrao_nao_contem_valor_de_chave(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mesmo com a env var da chave definida, o registro guarda só o nome.

        **Validates: Requirements 10.2**
        """
        # Define um valor de chave "real" no ambiente; ele NÃO deve aparecer no config.
        segredo = "sk-valor-secreto-que-nao-deve-vazar"
        monkeypatch.setenv("OPENAI_API_KEY", segredo)
        monkeypatch.setenv("GROQ_API_KEY", segredo)
        registry = default_provider_registry()
        for cfg in registry.values():
            assert cfg.api_key_env != segredo
            assert segredo not in cfg.api_key_env
            assert segredo not in cfg.model_id
            assert segredo not in cfg.name


class TestPropriedadesDeConfig:
    """Propriedades gerais de parsing e do registro de providers."""

    @given(
        order=st.lists(
            st.sampled_from(["openai", "groq"]), min_size=1, max_size=6
        )
    )
    def test_parsing_preserva_ordem_de_providers_conhecidos(
        self, order: list
    ) -> None:
        """A `provider_order` resolvida preserva a ordem dos itens conhecidos.

        Usa um `MonkeyPatch` como context manager (em vez da fixture de escopo de
        função) para que o ambiente seja reisolado a cada input gerado pelo
        `@given`, evitando o health check do Hypothesis.

        **Validates: Requirements 4.1**
        """
        with pytest.MonkeyPatch.context() as mp:
            # Isola de um eventual .env e da env real durante a propriedade.
            for name in _CONFIG_ENV_VARS:
                mp.delenv(name, raising=False)
            mp.setattr(
                "suporte.infrastructure.config.load_dotenv", lambda *a, **k: False
            )
            mp.setenv(ENV_PROVIDER_ORDER, ",".join(order))
            settings = load_settings()
        assert settings.provider_order == order

    def test_settings_pode_ser_construido_diretamente(self) -> None:
        # Sanidade do value object independente de env.
        settings = Settings(
            provider_order=["openai"],
            providers=default_provider_registry(),
        )
        assert settings.provider_order == ["openai"]
        assert settings.per_attempt_timeout_s == DEFAULT_PER_ATTEMPT_TIMEOUT_S
