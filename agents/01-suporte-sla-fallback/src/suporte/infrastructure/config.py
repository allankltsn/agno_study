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
from dataclasses import dataclass, field
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

# --- RAG / Knowledge (plugável por env) --------------------------------------
# RAG é uma dependência de infraestrutura ligada no composition_root, controlada
# por env sem tocar em domain/ nem application/ (OCP + DIP), no mesmo espírito da
# troca de provider e dos endpoints AWS/OTLP.
# Liga/desliga o RAG por completo. Desligado, o agente roda sem `knowledge`.
ENV_RAG_ENABLED = "RAG_ENABLED"
# Seleciona o backend de vector DB: "lancedb" | "pgvector" | "s3vectors".
ENV_RAG_VECTOR_DB = "RAG_VECTOR_DB"
# Tipo de busca do vector DB: "vector" | "keyword" | "hybrid".
ENV_RAG_SEARCH_TYPE = "RAG_SEARCH_TYPE"
# Nome da tabela/coleção de vetores (comum aos backends).
ENV_RAG_TABLE_NAME = "RAG_TABLE_NAME"
# Id do modelo de embeddings (embedder OpenAI por padrão).
ENV_RAG_EMBEDDER_ID = "RAG_EMBEDDER_ID"
# LanceDB: diretório local do índice (dentro da pasta do agente).
ENV_RAG_LANCEDB_URI = "RAG_LANCEDB_URI"
# PgVector: DSN de conexão (contém credenciais — NUNCA logado).
ENV_RAG_PG_DSN = "RAG_PG_DSN"
# S3Vectors: nome do bucket/índice de vetores (AWS-native, experimental).
ENV_RAG_S3_BUCKET = "RAG_S3_VECTOR_BUCKET"

# --- Persistência (memory & sessions do agente, plugável por env) ------------
# O Db do Agno persiste sessions (histórico) e memories (fatos do usuário). Por
# preferência do projeto, o backend padrão é DynamoDB; SQLite fica como fallback
# local sem AWS. Como o RAG, é dependência de infraestrutura ligada no
# composition_root — domain/application nunca a conhecem (DIP).
# Backend do Db: "dynamodb" (padrão) | "sqlite".
ENV_AGENT_DB_BACKEND = "AGENT_DB_BACKEND"
# Liga a memória de usuário (fatos duráveis por user_id). Sessions ficam sempre
# habilitadas quando há Db; a memória é opcional (custa chamadas extras ao modelo).
ENV_AGENT_MEMORY_ENABLED = "AGENT_MEMORY_ENABLED"
# DynamoDB: nomes das tabelas de sessions e memories (o Agno as cria no 1º uso).
ENV_AGENT_DDB_SESSION_TABLE = "AGENT_DDB_SESSION_TABLE"
ENV_AGENT_DDB_MEMORY_TABLE = "AGENT_DDB_MEMORY_TABLE"
# SQLite (fallback local): arquivo do banco dentro da pasta do agente.
ENV_AGENT_SQLITE_FILE = "AGENT_SQLITE_FILE"
# Endpoint/região AWS reaproveitados do restante do agente (LocalStack em dev).
# O endpoint vazio/ausente usa a AWS real (produção). Credenciais são lidas pelo
# boto3 do ambiente; nunca manipuladas nem logadas aqui.
ENV_AWS_ENDPOINT_URL = "AWS_ENDPOINT_URL"
ENV_AWS_REGION = "AWS_REGION"

# --- Defaults (aplicados quando a env var está ausente/vazia) ----------------
DEFAULT_PROVIDER_ORDER = "openai,groq"
DEFAULT_PER_ATTEMPT_TIMEOUT_S = 10.0  # RNF-01 / Req 6.1
DEFAULT_TOTAL_TIMEOUT_S = 25.0  # RNF-01 / Req 6.3
DEFAULT_MAX_ATTEMPTS_PER_PROVIDER = 2  # RNF-02 / Req 7.1
DEFAULT_BACKOFF_BASE_S = 0.5  # RNF-02 / Req 7.2
# Valores de `APP_ENV` que caracterizam ambiente de desenvolvimento (Req 16.6).
# Fora deles (ou com a env ausente), `debug_mode` é False por padrão (Req 16.7).
_DEV_ENV_VALUES = frozenset({"dev", "development", "local"})

# --- Defaults do RAG ---------------------------------------------------------
DEFAULT_RAG_ENABLED = False  # RAG desligado por padrão (agente funciona sem base).
DEFAULT_RAG_VECTOR_DB = "lancedb"  # backend local, sem servidor (default de estudo).
DEFAULT_RAG_SEARCH_TYPE = "hybrid"  # bom padrão (vetorial + keyword).
DEFAULT_RAG_TABLE_NAME = "estudo"
DEFAULT_RAG_EMBEDDER_ID = "text-embedding-3-small"
DEFAULT_RAG_LANCEDB_URI = "data/lancedb"
# Backends de vector DB suportados. `s3vectors` é AWS-native/experimental: o
# composition_root falha rápido explicando a limitação se o suporte não existir
# na versão instalada da Agno (nunca finge suporte silenciosamente).
_SUPPORTED_VECTOR_DBS = frozenset({"lancedb", "pgvector", "s3vectors"})
_SUPPORTED_SEARCH_TYPES = frozenset({"vector", "keyword", "hybrid"})

# --- Defaults da persistência (Db) -------------------------------------------
# DynamoDB é o backend PADRÃO (preferência do projeto): sessions/memories vão para
# o DynamoDB tanto em dev (LocalStack via AWS_ENDPOINT_URL) quanto em produção.
DEFAULT_AGENT_DB_BACKEND = "dynamodb"
DEFAULT_AGENT_MEMORY_ENABLED = True
DEFAULT_AGENT_DDB_SESSION_TABLE = "suporte-sla-sessions"
DEFAULT_AGENT_DDB_MEMORY_TABLE = "suporte-sla-memories"
DEFAULT_AGENT_SQLITE_FILE = "data/agent.db"
_SUPPORTED_DB_BACKENDS = frozenset({"dynamodb", "sqlite"})

# Valores "verdadeiros" aceitos em envs booleanas (case-insensitive).
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


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
class RagConfig:
    """Configuração do RAG/Knowledge, derivada do ambiente (plugável por env).

    Value object imutável que descreve **se** o RAG está ligado e **qual** backend
    de vector DB usar, sem que domínio/aplicação conheçam o concreto (DIP). Trocar
    `vector_db` de ``lancedb`` para ``pgvector`` (ou ``s3vectors``) reordena a
    infraestrutura sem tocar nas camadas internas (OCP).

    Segredos: o DSN do PgVector (`pg_dsn`) contém credenciais e **nunca** é logado
    nem incluído em mensagens de erro; erros de configuração nomeiam apenas a env
    var (`RAG_PG_DSN`), no mesmo padrão de `ProviderConfig.api_key_env`.

    Attributes:
        enabled: Liga/desliga o RAG (`RAG_ENABLED`). Quando ``False``, o
            `composition_root` monta o agente **sem** `knowledge`.
        vector_db: Backend selecionado: ``"lancedb"`` | ``"pgvector"`` |
            ``"s3vectors"`` (`RAG_VECTOR_DB`).
        search_type: Tipo de busca: ``"vector"`` | ``"keyword"`` | ``"hybrid"``
            (`RAG_SEARCH_TYPE`).
        table_name: Nome da tabela/coleção de vetores (`RAG_TABLE_NAME`).
        embedder_id: Id do modelo de embeddings (`RAG_EMBEDDER_ID`).
        lancedb_uri: Diretório local do índice LanceDB (`RAG_LANCEDB_URI`); usado
            apenas quando ``vector_db == "lancedb"``.
        pg_dsn: DSN de conexão do PgVector (`RAG_PG_DSN`); obrigatório e não-vazio
            quando ``vector_db == "pgvector"``. Contém credenciais — nunca logado.
        s3_vector_bucket: Bucket/índice do S3 Vectors (`RAG_S3_VECTOR_BUCKET`);
            obrigatório quando ``vector_db == "s3vectors"``.
    """

    enabled: bool = DEFAULT_RAG_ENABLED
    vector_db: str = DEFAULT_RAG_VECTOR_DB
    search_type: str = DEFAULT_RAG_SEARCH_TYPE
    table_name: str = DEFAULT_RAG_TABLE_NAME
    embedder_id: str = DEFAULT_RAG_EMBEDDER_ID
    lancedb_uri: str = DEFAULT_RAG_LANCEDB_URI
    pg_dsn: Optional[str] = None
    s3_vector_bucket: Optional[str] = None


@dataclass(frozen=True)
class PersistenceConfig:
    """Configuração de persistência (memory & sessions), derivada do ambiente.

    Value object imutável que descreve **onde** o Agno persiste as conversas
    (sessions) e os fatos do usuário (memories). Por preferência do projeto, o
    backend padrão é ``dynamodb``; ``sqlite`` fica como fallback local sem AWS.
    Trocar o backend não toca em `domain/` nem `application/` (OCP + DIP).

    Credenciais AWS **não** ficam aqui: o boto3 as lê do ambiente no momento da
    chamada. `endpoint_url` aponta o DynamoDB para o LocalStack em dev (via
    `AWS_ENDPOINT_URL`); vazio/ausente usa a AWS real (produção).

    Attributes:
        backend: ``"dynamodb"`` (padrão) | ``"sqlite"`` (`AGENT_DB_BACKEND`).
        memory_enabled: Liga a memória de usuário (`AGENT_MEMORY_ENABLED`). As
            sessions ficam sempre habilitadas quando há Db; a memória é opcional.
        ddb_session_table: Tabela DynamoDB de sessions (`AGENT_DDB_SESSION_TABLE`).
        ddb_memory_table: Tabela DynamoDB de memories (`AGENT_DDB_MEMORY_TABLE`).
        sqlite_file: Arquivo do SQLite quando ``backend == "sqlite"``
            (`AGENT_SQLITE_FILE`).
        aws_endpoint_url: Endpoint do DynamoDB (`AWS_ENDPOINT_URL`); ``None`` usa a
            AWS real. Em dev aponta para o LocalStack (ex.: http://localhost:4566).
        aws_region: Região AWS (`AWS_REGION`), usada ao construir o client boto3.
    """

    backend: str = DEFAULT_AGENT_DB_BACKEND
    memory_enabled: bool = DEFAULT_AGENT_MEMORY_ENABLED
    ddb_session_table: str = DEFAULT_AGENT_DDB_SESSION_TABLE
    ddb_memory_table: str = DEFAULT_AGENT_DDB_MEMORY_TABLE
    sqlite_file: str = DEFAULT_AGENT_SQLITE_FILE
    aws_endpoint_url: Optional[str] = None
    aws_region: Optional[str] = None


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
        rag: Configuração do RAG/Knowledge (`RagConfig`), plugável por env. Quando
            ``rag.enabled`` é ``False`` (padrão), o agente é montado sem base de
            conhecimento; caso contrário, `rag.vector_db` seleciona o backend.
        persistence: Configuração de memory & sessions (`PersistenceConfig`),
            plugável por env. Backend padrão ``dynamodb``; ``sqlite`` como fallback
            local.
    """

    provider_order: List[str]
    providers: Dict[str, ProviderConfig]
    per_attempt_timeout_s: float = DEFAULT_PER_ATTEMPT_TIMEOUT_S
    total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S
    max_attempts_per_provider: int = DEFAULT_MAX_ATTEMPTS_PER_PROVIDER
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    otel_endpoint: Optional[str] = None
    debug_mode: bool = False
    rag: RagConfig = field(default_factory=RagConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)


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


def _get_bool(env_name: str, default: bool) -> bool:
    """Lê um booleano de env, aplicando o default quando ausente.

    Aceita (case-insensitive) ``1/true/yes/on`` como verdadeiro e
    ``0/false/no/off``/vazio como falso.

    Args:
        env_name: Nome da variável de ambiente.
        default: Valor usado quando a variável está ausente.

    Returns:
        O valor booleano interpretado.

    Raises:
        ValueError: Se o valor presente não for reconhecido como booleano,
            nomeando a variável de ambiente infratora.
    """
    raw = os.getenv(env_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{env_name} deve ser booleano (1/true/yes/on ou 0/false/no/off); "
        f"valor inválido: {raw!r}"
    )


def _get_str(env_name: str, default: str) -> str:
    """Lê uma string de env, aplicando o default quando ausente/vazia.

    Args:
        env_name: Nome da variável de ambiente.
        default: Valor usado quando a variável está ausente ou só com espaços.

    Returns:
        O valor sem espaços em volta, ou o default.
    """
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _get_optional_str(env_name: str) -> Optional[str]:
    """Lê uma string opcional de env; ``None`` quando ausente/vazia.

    Args:
        env_name: Nome da variável de ambiente.

    Returns:
        O valor sem espaços em volta, ou ``None`` se ausente/vazio.
    """
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _load_rag_config() -> RagConfig:
    """Monta `RagConfig` a partir das variáveis de ambiente do RAG (plugável).

    Lê o toggle `RAG_ENABLED` e, quando ligado, o backend em `RAG_VECTOR_DB` e os
    parâmetros do backend. Valida o backend/tipo de busca contra os conjuntos
    suportados e exige a configuração obrigatória do backend selecionado,
    **nomeando** a env var ausente — sem jamais expor o valor de segredos (o DSN
    do PgVector nunca é logado nem incluído em mensagens de erro).

    Quando `RAG_ENABLED` é falso/ausente, retorna um `RagConfig` desabilitado com
    defaults e **não** valida os campos de backend (o agente roda sem knowledge).

    Returns:
        Um `RagConfig` imutável e validado.

    Raises:
        ValueError: Se `RAG_ENABLED` for inválido; se `RAG_VECTOR_DB`/
            `RAG_SEARCH_TYPE` não estiverem entre os suportados; ou se faltar a
            configuração obrigatória do backend selecionado (`RAG_PG_DSN` para
            pgvector, `RAG_S3_VECTOR_BUCKET` para s3vectors) — nomeando a env var.
    """
    enabled = _get_bool(ENV_RAG_ENABLED, DEFAULT_RAG_ENABLED)

    vector_db = _get_str(ENV_RAG_VECTOR_DB, DEFAULT_RAG_VECTOR_DB).lower()
    search_type = _get_str(ENV_RAG_SEARCH_TYPE, DEFAULT_RAG_SEARCH_TYPE).lower()
    table_name = _get_str(ENV_RAG_TABLE_NAME, DEFAULT_RAG_TABLE_NAME)
    embedder_id = _get_str(ENV_RAG_EMBEDDER_ID, DEFAULT_RAG_EMBEDDER_ID)
    lancedb_uri = _get_str(ENV_RAG_LANCEDB_URI, DEFAULT_RAG_LANCEDB_URI)
    pg_dsn = _get_optional_str(ENV_RAG_PG_DSN)
    s3_vector_bucket = _get_optional_str(ENV_RAG_S3_BUCKET)

    # Com o RAG desligado, não validamos os campos do backend: devolve o objeto
    # desabilitado (o composition_root nem monta o Knowledge nesse caso).
    if not enabled:
        return RagConfig(
            enabled=False,
            vector_db=vector_db,
            search_type=search_type,
            table_name=table_name,
            embedder_id=embedder_id,
            lancedb_uri=lancedb_uri,
            pg_dsn=pg_dsn,
            s3_vector_bucket=s3_vector_bucket,
        )

    # RAG ligado: valida backend e tipo de busca, e a config obrigatória do backend.
    if vector_db not in _SUPPORTED_VECTOR_DBS:
        known = ", ".join(sorted(_SUPPORTED_VECTOR_DBS))
        raise ValueError(
            f"Backend de vector DB desconhecido em {ENV_RAG_VECTOR_DB}: "
            f"{vector_db!r}. Suportados: {known}."
        )
    if search_type not in _SUPPORTED_SEARCH_TYPES:
        known = ", ".join(sorted(_SUPPORTED_SEARCH_TYPES))
        raise ValueError(
            f"Tipo de busca desconhecido em {ENV_RAG_SEARCH_TYPE}: "
            f"{search_type!r}. Suportados: {known}."
        )

    if vector_db == "pgvector" and not pg_dsn:
        # Nomeia a env var ausente; NUNCA loga o valor do DSN (contém senha).
        raise ValueError(
            f"Configuração ausente: {ENV_RAG_PG_DSN} é obrigatória quando "
            f"{ENV_RAG_VECTOR_DB}=pgvector. Defina a DSN de conexão e tente novamente."
        )
    if vector_db == "s3vectors" and not s3_vector_bucket:
        raise ValueError(
            f"Configuração ausente: {ENV_RAG_S3_BUCKET} é obrigatória quando "
            f"{ENV_RAG_VECTOR_DB}=s3vectors. Defina o bucket/índice de vetores."
        )

    return RagConfig(
        enabled=True,
        vector_db=vector_db,
        search_type=search_type,
        table_name=table_name,
        embedder_id=embedder_id,
        lancedb_uri=lancedb_uri,
        pg_dsn=pg_dsn,
        s3_vector_bucket=s3_vector_bucket,
    )


def _load_persistence_config() -> PersistenceConfig:
    """Monta `PersistenceConfig` a partir das variáveis de ambiente (plugável).

    Lê o backend em `AGENT_DB_BACKEND` (padrão ``dynamodb``) e valida contra os
    suportados, o toggle de memória (`AGENT_MEMORY_ENABLED`), os nomes de tabelas
    do DynamoDB e o arquivo do SQLite. O endpoint/região AWS são reaproveitados de
    `AWS_ENDPOINT_URL`/`AWS_REGION` (LocalStack em dev, AWS real em prod).

    Credenciais AWS nunca são lidas aqui — o boto3 as obtém do ambiente na hora da
    chamada e este módulo jamais as loga ou guarda (Req 10.6, 11.5).

    Returns:
        Um `PersistenceConfig` imutável e validado.

    Raises:
        ValueError: Se `AGENT_DB_BACKEND` não estiver entre os suportados
            (nomeando a env var e os valores válidos) ou se `AGENT_MEMORY_ENABLED`
            for um booleano inválido.
    """
    backend = _get_str(ENV_AGENT_DB_BACKEND, DEFAULT_AGENT_DB_BACKEND).lower()
    if backend not in _SUPPORTED_DB_BACKENDS:
        known = ", ".join(sorted(_SUPPORTED_DB_BACKENDS))
        raise ValueError(
            f"Backend de persistência desconhecido em {ENV_AGENT_DB_BACKEND}: "
            f"{backend!r}. Suportados: {known}."
        )

    return PersistenceConfig(
        backend=backend,
        memory_enabled=_get_bool(ENV_AGENT_MEMORY_ENABLED, DEFAULT_AGENT_MEMORY_ENABLED),
        ddb_session_table=_get_str(
            ENV_AGENT_DDB_SESSION_TABLE, DEFAULT_AGENT_DDB_SESSION_TABLE
        ),
        ddb_memory_table=_get_str(
            ENV_AGENT_DDB_MEMORY_TABLE, DEFAULT_AGENT_DDB_MEMORY_TABLE
        ),
        sqlite_file=_get_str(ENV_AGENT_SQLITE_FILE, DEFAULT_AGENT_SQLITE_FILE),
        # Endpoint vazio/ausente => AWS real (produção). Em dev, LocalStack.
        aws_endpoint_url=_get_optional_str(ENV_AWS_ENDPOINT_URL),
        aws_region=_get_optional_str(ENV_AWS_REGION),
    )


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
        - ``RAG_ENABLED`` / ``RAG_VECTOR_DB`` / ``RAG_SEARCH_TYPE`` /
          ``RAG_TABLE_NAME`` / ``RAG_EMBEDDER_ID`` / ``RAG_LANCEDB_URI`` /
          ``RAG_PG_DSN`` / ``RAG_S3_VECTOR_BUCKET`` -> ``rag`` (RagConfig)
        - ``AGENT_DB_BACKEND`` / ``AGENT_MEMORY_ENABLED`` /
          ``AGENT_DDB_SESSION_TABLE`` / ``AGENT_DDB_MEMORY_TABLE`` /
          ``AGENT_SQLITE_FILE`` / ``AWS_ENDPOINT_URL`` / ``AWS_REGION``
          -> ``persistence`` (PersistenceConfig)

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
        rag=_load_rag_config(),
        persistence=_load_persistence_config(),
    )
