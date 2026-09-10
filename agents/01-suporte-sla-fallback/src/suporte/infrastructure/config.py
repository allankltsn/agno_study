"""Configuração tipada lida de variáveis de ambiente.

Este módulo traduz variáveis de ambiente em um objeto `Settings` imutável e
tipado (RNF-04). É o único lugar que conhece os *nomes* das variáveis de
ambiente de configuração do agente; o restante do código depende de `Settings`,
não de `os.getenv`.

Princípios aplicados:
- **Segredos fora do código (Req 10)**: `ProviderConfig` guarda apenas o *nome*
  da variável de ambiente da chave (`api_key_env`), nunca o valor. A leitura do
  valor da chave acontece só em tempo de execução, no adapter, e nunca é logada.
- **Troca de provider por env (Req 4)**: `MODEL_PROVIDER_ORDER` define a ordem de
  fallback. Alterar essa variável reordena os providers sem tocar em `domain/`
  nem em `application/` (OCP + DIP).
- **SLA previsível (Req 6)**: timeouts, tentativas e backoff vêm de env, com
  defaults sensatos; `total_timeout_s` nunca pode ser menor que
  `per_attempt_timeout_s`.

Sem imports de Agno nem de SDKs de provider: este módulo é infraestrutura pura de
configuração e permanece testável sem rede.

Compatibilidade: tipos de `typing` (`Dict`, `List`) e `from __future__ import
annotations` para funcionar em Python 3.9+.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv

# --- Nomes das variáveis de ambiente (nunca valores) -------------------------
# Centralizados como constantes para evitar strings mágicas espalhadas e
# facilitar a documentação (.env.example) e os testes.
ENV_PROVIDER_ORDER = "MODEL_PROVIDER_ORDER"
ENV_PER_ATTEMPT_TIMEOUT = "LLM_PER_ATTEMPT_TIMEOUT"
ENV_TOTAL_TIMEOUT = "LLM_TOTAL_TIMEOUT"
ENV_MAX_ATTEMPTS = "LLM_MAX_ATTEMPTS"
ENV_BACKOFF_BASE = "LLM_BACKOFF_BASE"
# Endpoint OTLP do tracing OpenTelemetry (Req 16.1). O destino (collector local
# ou AWS) é trocado só por esta env var, sem tocar no código do agente.
ENV_OTEL_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
# Ambiente de execução: quando vale "dev"/"development", habilita o `debug_mode`
# do Agno (Req 16.6); em qualquer outro valor (ou ausente) o debug fica desligado
# por padrão (Req 16.7).
ENV_APP_ENV = "APP_ENV"

# --- Defaults (aplicados quando a env var está ausente/vazia) ----------------
DEFAULT_PROVIDER_ORDER = "openai,groq"
DEFAULT_PER_ATTEMPT_TIMEOUT_S = 10.0  # RNF-01 / Req 6.1
DEFAULT_TOTAL_TIMEOUT_S = 25.0  # RNF-01 / Req 6.3
DEFAULT_MAX_ATTEMPTS_PER_PROVIDER = 2  # RNF-02 / Req 7.1
DEFAULT_BACKOFF_BASE_S = 0.5  # RNF-02 / Req 7.2
# Valores de `APP_ENV` que caracterizam ambiente de desenvolvimento (Req 16.6).
# Fora deles (ou com a env ausente), `debug_mode` é False por padrão (Req 16.7).
_DEV_ENV_VALUES = frozenset({"dev", "development", "local"})


@dataclass(frozen=True)
class ProviderConfig:
    """Descreve um provider de LLM disponível para o agente.

    É um value object imutável (`frozen=True`). Note que **não** guarda a chave de
    API: apenas o *nome* da variável de ambiente de onde a chave será lida em
    tempo de execução (`api_key_env`). Isso satisfaz o Req 10.2 — nenhum literal
    de chave no código, nenhuma chave em memória a mais do que o necessário.

    Attributes:
        name: Identificador do provider (ex.: ``"openai"``, ``"groq"``,
            ``"ollama"``). É a chave usada em `MODEL_PROVIDER_ORDER` e em
            `Settings.providers`.
        model_id: Id do modelo a ser usado (ex.: ``"gpt-4o-mini"``,
            ``"llama-3.3-70b-versatile"``).
        api_key_env: **Nome** da variável de ambiente que contém a chave de API
            (ex.: ``"OPENAI_API_KEY"``), nunca o valor da chave.
    """

    name: str
    model_id: str
    api_key_env: str


@dataclass(frozen=True)
class Settings:
    """Configuração tipada e imutável do agente, derivada do ambiente.

    Agrupa a ordem de fallback, o registro de providers e os parâmetros de SLA e
    resiliência. Por ser `frozen=True`, uma vez montada em `load_settings` não
    pode ser mutada acidentalmente enquanto flui pelo `composition_root` e pelo
    `FallbackLLM`.

    Attributes:
        provider_order: Ordem de tentativa/fallback dos providers, ex.:
            ``["openai", "groq"]``. O primeiro é o primário; os demais são
            secundários, tentados por fallback (Req 4.1, 5.2).
        providers: Registro `nome -> ProviderConfig` com todos os providers
            conhecidos. Todo nome em `provider_order` deve existir aqui.
        per_attempt_timeout_s: Timeout máximo de uma única tentativa a um provider,
            em segundos (RNF-01 / Req 6.1). Default 10.0.
        total_timeout_s: Limite superior de tempo para a requisição inteira,
            incluindo fallback, em segundos (RNF-01 / Req 6.3). Default 25.0.
            Invariante: ``total_timeout_s >= per_attempt_timeout_s`` (Req 6.4).
        max_attempts_per_provider: Número de tentativas por provider antes de
            passar ao próximo (RNF-02 / Req 7.1). Default 2.
        backoff_base_s: Base do backoff exponencial entre tentativas, em segundos
            (RNF-02 / Req 7.2). Default 0.5.
        otel_endpoint: Endpoint OTLP do tracing OpenTelemetry, lido de
            `OTEL_EXPORTER_OTLP_ENDPOINT` (Req 16.1). ``None`` quando a env var
            está ausente/vazia — nesse caso o `composition_root` liga o tracing
            desabilitado (aviso + segue sem exportar, Req 16.2). Trocar o destino
            (collector local ↔ AWS) é só mudar essa env var, sem tocar no código.
        debug_mode: Liga o `debug_mode` do Agno. ``True`` apenas quando `APP_ENV`
            indica desenvolvimento (Req 16.6); ``False`` por padrão em qualquer
            outro ambiente (Req 16.7).
    """

    provider_order: List[str]
    providers: Dict[str, ProviderConfig]
    per_attempt_timeout_s: float = DEFAULT_PER_ATTEMPT_TIMEOUT_S
    total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S
    max_attempts_per_provider: int = DEFAULT_MAX_ATTEMPTS_PER_PROVIDER
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    otel_endpoint: Optional[str] = None
    debug_mode: bool = False


def default_provider_registry() -> Dict[str, ProviderConfig]:
    """Registro padrão de providers conhecidos.

    Cada `ProviderConfig` referencia apenas o **nome** da variável de ambiente da
    chave (Req 10.2). Os `model_id` são defaults sensatos e podem ser sobrescritos
    em evoluções futuras; o ponto de estudo é o *arranjo* (troca por env), não o
    modelo específico.

    Returns:
        Mapa `nome -> ProviderConfig` com os providers ``openai``, ``groq`` e ``ollama``.
    """
    return {
        "openai": ProviderConfig(
            name="openai",
            model_id="gpt-4o-mini",
            api_key_env="OPENAI_API_KEY",
        ),
        "groq": ProviderConfig(
            name="groq",
            model_id="llama-3.3-70b-versatile",
            api_key_env="GROQ_API_KEY",
        ),
        # Provider local (Ollama): NAO usa chave de API — o endpoint vem de
        # OLLAMA_HOST (lido no composition_root). `api_key_env` e mantido por
        # uniformidade do value object, mas nunca e verificado para o ollama
        # (o composition_root nao chama _require_api_key nesse ramo).
        "ollama": ProviderConfig(
            name="ollama",
            model_id="llama3.2",
            api_key_env="OLLAMA_API_KEY",
        ),
    }


def _parse_order(raw: str) -> List[str]:
    """Interpreta `MODEL_PROVIDER_ORDER` como lista separada por vírgulas.

    Divide por vírgula, remove espaços em volta de cada item e descarta itens
    vazios (tolerando entradas como ``"openai, groq,"`` ou ``"openai,,groq"``).

    Args:
        raw: Valor cru da variável de ambiente.

    Returns:
        Lista de nomes de provider na ordem informada, sem itens vazios.
    """
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_float(env_name: str, default: float) -> float:
    """Lê um float de env, aplicando o default quando ausente/vazio.

    Args:
        env_name: Nome da variável de ambiente.
        default: Valor usado quando a variável está ausente ou vazia.

    Returns:
        O valor convertido para ``float``.

    Raises:
        ValueError: Se o valor presente não puder ser convertido para float,
            nomeando a variável de ambiente infratora.
    """
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{env_name} deve ser um número (segundos); valor inválido: {raw!r}"
        ) from exc


def _get_int(env_name: str, default: int) -> int:
    """Lê um inteiro de env, aplicando o default quando ausente/vazio.

    Args:
        env_name: Nome da variável de ambiente.
        default: Valor usado quando a variável está ausente ou vazia.

    Returns:
        O valor convertido para ``int``.

    Raises:
        ValueError: Se o valor presente não puder ser convertido para int,
            nomeando a variável de ambiente infratora.
    """
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{env_name} deve ser um inteiro; valor inválido: {raw!r}"
        ) from exc


def _get_otel_endpoint() -> Optional[str]:
    """Lê o endpoint OTLP de `OTEL_EXPORTER_OTLP_ENDPOINT` (Req 16.1).

    Retorna o valor sem espaços em volta; ``None`` quando a env var está ausente
    ou vazia (apenas espaços). O `composition_root` repassa esse valor a
    `init_tracing`, que desabilita o tracing e segue sem interromper quando o
    endpoint não está configurado (Req 16.2).

    Returns:
        O endpoint OTLP como string, ou ``None`` se ausente/vazio.
    """
    raw = os.getenv(ENV_OTEL_ENDPOINT)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _get_debug_mode() -> bool:
    """Deriva o `debug_mode` do Agno a partir de `APP_ENV` (Req 16.6/16.7).

    Retorna ``True`` somente quando `APP_ENV` (sem diferenciar maiúsculas) indica
    ambiente de desenvolvimento (``dev``/``development``/``local``); em qualquer
    outro valor, ou com a env ausente, retorna ``False`` — debug desligado por
    padrão fora de dev.

    Returns:
        ``True`` em ambiente de desenvolvimento; ``False`` caso contrário.
    """
    raw = os.getenv(ENV_APP_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _DEV_ENV_VALUES


def load_settings() -> Settings:
    """Monta `Settings` a partir das variáveis de ambiente.

    Carrega um eventual arquivo ``.env`` (via `python-dotenv`) e então lê as
    variáveis com `os.getenv`, aplicando defaults e validando invariantes. A
    ordem de providers vem de `MODEL_PROVIDER_ORDER` (lista separada por vírgula);
    quando ausente/vazia, usa `DEFAULT_PROVIDER_ORDER` (que contém >= 1 provider).

    Variáveis lidas:
        - ``MODEL_PROVIDER_ORDER``   -> ``provider_order`` (ex.: ``openai,groq``)
        - ``LLM_PER_ATTEMPT_TIMEOUT`` -> ``per_attempt_timeout_s`` (default 10.0)
        - ``LLM_TOTAL_TIMEOUT``       -> ``total_timeout_s`` (default 25.0)
        - ``LLM_MAX_ATTEMPTS``        -> ``max_attempts_per_provider`` (default 2)
        - ``LLM_BACKOFF_BASE``        -> ``backoff_base_s`` (default 0.5)
        - ``OTEL_EXPORTER_OTLP_ENDPOINT`` -> ``otel_endpoint`` (None se ausente)
        - ``APP_ENV``                 -> ``debug_mode`` (True só em dev)

    Returns:
        Um `Settings` imutável e validado.

    Raises:
        ValueError: Se `provider_order` referenciar um provider desconhecido
            (nomeando o provider, Req 4.5); se a ordem resolvida ficar vazia
            (Req 4.6); ou se ``total_timeout_s < per_attempt_timeout_s``
            (Req 6.4). Também se um valor numérico de env for inválido.
    """
    # Carrega .env se existir; não sobrescreve variáveis já definidas no ambiente
    # real (útil em produção/CI, onde a env tem precedência sobre o arquivo).
    load_dotenv(override=False)

    providers = default_provider_registry()

    raw_order = os.getenv(ENV_PROVIDER_ORDER)
    if raw_order is None or raw_order.strip() == "":
        raw_order = DEFAULT_PROVIDER_ORDER
    provider_order = _parse_order(raw_order)

    # Req 4.6: a ordem resolvida não pode ser vazia (ao menos um provider).
    if not provider_order:
        raise ValueError(
            f"{ENV_PROVIDER_ORDER} resolveu para uma lista vazia; "
            "ao menos um provider é obrigatório."
        )

    # Req 4.5: todo provider da ordem precisa ter um ProviderConfig registrado.
    for name in provider_order:
        if name not in providers:
            known = ", ".join(sorted(providers)) or "(nenhum)"
            raise ValueError(
                f"Provider desconhecido em {ENV_PROVIDER_ORDER}: {name!r}. "
                f"Providers conhecidos: {known}."
            )

    per_attempt_timeout_s = _get_float(
        ENV_PER_ATTEMPT_TIMEOUT, DEFAULT_PER_ATTEMPT_TIMEOUT_S
    )
    total_timeout_s = _get_float(ENV_TOTAL_TIMEOUT, DEFAULT_TOTAL_TIMEOUT_S)
    max_attempts_per_provider = _get_int(
        ENV_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS_PER_PROVIDER
    )
    backoff_base_s = _get_float(ENV_BACKOFF_BASE, DEFAULT_BACKOFF_BASE_S)

    # Req 6.4: o limite total deve comportar ao menos uma tentativa completa.
    if total_timeout_s < per_attempt_timeout_s:
        raise ValueError(
            "Configuração inválida: total_timeout_s "
            f"({total_timeout_s}) < per_attempt_timeout_s ({per_attempt_timeout_s}). "
            "É exigido total_timeout_s >= per_attempt_timeout_s."
        )

    return Settings(
        provider_order=provider_order,
        providers=providers,
        per_attempt_timeout_s=per_attempt_timeout_s,
        total_timeout_s=total_timeout_s,
        max_attempts_per_provider=max_attempts_per_provider,
        backoff_base_s=backoff_base_s,
        otel_endpoint=_get_otel_endpoint(),
        debug_mode=_get_debug_mode(),
    )
