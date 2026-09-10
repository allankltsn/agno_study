"""Fábrica do vector DB do RAG — o único lugar que conhece os backends concretos.

Traduz a `RagConfig` (tipada, vinda de `config.py`) no vector DB concreto da Agno,
selecionado por env (`RAG_VECTOR_DB`): ``lancedb`` | ``pgvector`` | ``s3vectors``.
Trocar o backend **não** toca em `domain/` nem `application/` — apenas a env muda
(OCP + DIP), no mesmo espírito da troca de provider de LLM.

Imports tardios (Req 12.6): a Agno e os SDKs de backend são importados **dentro**
das funções, então este módulo permanece importável sem o SDK instalado e os tipos
da Agno ficam confinados à `infrastructure`.

Segredos (Req 10.6, 11.5): o DSN do PgVector vem de `RagConfig.pg_dsn` (lido de
`RAG_PG_DSN`) e é repassado direto ao backend; **nunca** é logado nem incluído em
mensagens de erro. As mensagens de falha nomeiam apenas a env var / o backend.
"""

from __future__ import annotations

from typing import Any

from suporte.infrastructure.config import RagConfig


def _resolve_search_type(search_type: str) -> Any:
    """Mapeia a string de busca para o enum `SearchType` da Agno (import tardio).

    Args:
        search_type: ``"vector"`` | ``"keyword"`` | ``"hybrid"`` (já validado em
            `config.py`).

    Returns:
        O membro correspondente de ``agno.vectordb.search.SearchType``.
    """
    # O enum SearchType é compartilhado pelos vector DBs da Agno.
    from agno.vectordb.search import SearchType

    return {
        "vector": SearchType.vector,
        "keyword": SearchType.keyword,
        "hybrid": SearchType.hybrid,
    }[search_type]


def _build_embedder(cfg: RagConfig) -> Any:
    """Constrói o embedder (OpenAI por padrão) a partir da `RagConfig`.

    Import tardio do embedder da Agno. A chave da OpenAI é lida pelo próprio SDK do
    ambiente no momento do embedding — nunca manipulada aqui.

    Args:
        cfg: Configuração do RAG (usa `cfg.embedder_id`).

    Returns:
        Uma instância de embedder da Agno.
    """
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    return OpenAIEmbedder(id=cfg.embedder_id)


def _build_lancedb(cfg: RagConfig) -> Any:
    """Monta o vector DB LanceDB (local, sem servidor) — default de estudo."""
    from agno.vectordb.lancedb import LanceDb

    return LanceDb(
        table_name=cfg.table_name,
        uri=cfg.lancedb_uri,
        search_type=_resolve_search_type(cfg.search_type),
        embedder=_build_embedder(cfg),
    )


def _build_pgvector(cfg: RagConfig) -> Any:
    """Monta o vector DB PgVector (Postgres + extensão `vector`).

    A `db_url` vem de `cfg.pg_dsn` (env `RAG_PG_DSN`), já validada como presente em
    `config.py` quando o backend é ``pgvector``. O DSN contém credenciais e não é
    logado aqui.
    """
    from agno.vectordb.pgvector import PgVector

    return PgVector(
        table_name=cfg.table_name,
        db_url=cfg.pg_dsn,
        search_type=_resolve_search_type(cfg.search_type),
        embedder=_build_embedder(cfg),
    )


def _build_s3vectors(cfg: RagConfig) -> Any:
    """Monta o vector DB S3 Vectors (AWS-native) — experimental.

    O S3 Vectors é um serviço AWS recente; o suporte nativo na Agno pode não existir
    ou não ser estável na versão instalada, e o LocalStack não o emula de forma
    confiável (ver skill `agno-knowledge-rag`). Em vez de fingir suporte, tentamos
    importar o backend e, se ele não estiver disponível, **falhamos rápido com uma
    mensagem clara** explicando a limitação e apontando os backends estáveis.

    Raises:
        RuntimeError: Se o backend S3 Vectors não estiver disponível na versão
            instalada da Agno. A mensagem orienta usar ``lancedb``/``pgvector``
            localmente e reservar ``s3vectors`` para produção AWS quando o suporte
            amadurecer.
    """
    try:
        # Import tardio e tolerante: o caminho/símbolo pode variar/não existir.
        from agno.vectordb.s3vectors import S3Vectors  # type: ignore
    except Exception as exc:  # ImportError e afins
        raise RuntimeError(
            "RAG_VECTOR_DB=s3vectors selecionado, mas o backend S3 Vectors não está "
            "disponível na versão instalada da Agno (e o LocalStack não o emula de "
            "forma confiável). Use RAG_VECTOR_DB=lancedb ou pgvector no ambiente "
            "local; reserve s3vectors para produção AWS quando o suporte estiver "
            "estável. Consulte a skill 'agno-knowledge-rag' para detalhes."
        ) from exc

    return S3Vectors(
        vector_bucket_name=cfg.s3_vector_bucket,
        table_name=cfg.table_name,
        search_type=_resolve_search_type(cfg.search_type),
        embedder=_build_embedder(cfg),
    )


def build_vector_db(cfg: RagConfig) -> Any:
    """Constrói o vector DB concreto selecionado por `cfg.vector_db`.

    Args:
        cfg: Configuração do RAG já validada por `config.py`. Deve estar habilitada
            (`cfg.enabled is True`); a checagem do toggle é responsabilidade do
            chamador (`composition_root`).

    Returns:
        O vector DB da Agno correspondente (LanceDb, PgVector ou S3Vectors).

    Raises:
        RuntimeError: Se `cfg.vector_db` não for suportado (defesa em profundidade;
            `config.py` já valida) ou se o backend S3 Vectors não estiver
            disponível na versão instalada.
    """
    builders = {
        "lancedb": _build_lancedb,
        "pgvector": _build_pgvector,
        "s3vectors": _build_s3vectors,
    }
    builder = builders.get(cfg.vector_db)
    if builder is None:
        # config.py já valida; isto é defesa em profundidade.
        raise RuntimeError(
            "Backend de vector DB não suportado no vector_db_factory: "
            "'{}'. Suportados: 'lancedb', 'pgvector', 's3vectors'.".format(cfg.vector_db)
        )
    return builder(cfg)


def build_knowledge(cfg: RagConfig, *, name: str = "Base de Suporte") -> Any:
    """Monta o `Knowledge` da Agno com o vector DB selecionado por env.

    Import tardio do `Knowledge`. O caso de uso e o domínio nunca veem este objeto
    diretamente; ele é injetado no `Agent` pelo `composition_root`.

    Args:
        cfg: Configuração do RAG já validada e habilitada.
        name: Nome amigável da base (aparece na UI/tracing).

    Returns:
        Uma instância de ``agno.knowledge.knowledge.Knowledge`` pronta para ligar
        ao `Agent`.
    """
    from agno.knowledge.knowledge import Knowledge

    return Knowledge(name=name, vector_db=build_vector_db(cfg))
