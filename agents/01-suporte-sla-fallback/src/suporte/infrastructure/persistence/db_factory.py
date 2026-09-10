"""Fábrica do `Db` do agente — memory & sessions persistidos por env.

Traduz a `PersistenceConfig` (tipada, vinda de `config.py`) no `Db` concreto do
Agno, selecionado por env (`AGENT_DB_BACKEND`): ``dynamodb`` (padrão) | ``sqlite``.
O `Db` do Agno persiste **sessions** (histórico da conversa) e **memories** (fatos
do usuário) — trocar o backend não toca em `domain/` nem `application/` (DIP).

Preferência do projeto: **DynamoDB por padrão**, tanto em dev (LocalStack via
`AWS_ENDPOINT_URL`) quanto em produção (AWS real). O Agno cria as tabelas e os
índices (GSIs) necessários no primeiro uso; o principal/credencial precisa ter
permissão de criação e leitura/escrita (ver IAM no template SAM).

Imports tardios (Req 12.6): a Agno e o boto3 são importados **dentro** das
funções, então este módulo permanece importável sem os SDKs instalados.

Segredos (Req 10.6, 11.5): credenciais AWS **não** são lidas nem manipuladas aqui
— o boto3 as obtém do ambiente na hora da chamada. Este módulo nunca loga
credenciais, endpoint com token, nem o conteúdo de sessions/memories.
"""

from __future__ import annotations

from typing import Any

from suporte.infrastructure.config import PersistenceConfig


def _build_dynamo_client(cfg: PersistenceConfig) -> Any:
    """Cria o client boto3 do DynamoDB apontando para o endpoint configurado.

    Em desenvolvimento, `cfg.aws_endpoint_url` aponta para o LocalStack; em
    produção fica ``None`` e o boto3 usa a AWS real. Região e credenciais vêm do
    ambiente (boto3), nunca deste módulo.

    Args:
        cfg: Configuração de persistência (usa endpoint/região).

    Returns:
        Um client boto3 do serviço ``dynamodb``.
    """
    import boto3

    # endpoint_url=None faz o boto3 usar o endpoint padrão da AWS (produção).
    return boto3.client(
        "dynamodb",
        endpoint_url=cfg.aws_endpoint_url,
        region_name=cfg.aws_region,
    )


def _build_dynamodb(cfg: PersistenceConfig) -> Any:
    """Monta o `DynamoDb` do Agno (backend padrão) para sessions e memories.

    Passa um client boto3 já configurado (endpoint/região) via `db_client`, e os
    nomes das tabelas de sessions e memories. O Agno cria as tabelas/GSIs no
    primeiro uso.

    Args:
        cfg: Configuração de persistência já validada.

    Returns:
        Uma instância de ``agno.db.dynamo.DynamoDb``.
    """
    from agno.db.dynamo import DynamoDb

    return DynamoDb(
        db_client=_build_dynamo_client(cfg),
        session_table=cfg.ddb_session_table,
        memory_table=cfg.ddb_memory_table,
        region_name=cfg.aws_region,
    )


def _build_sqlite(cfg: PersistenceConfig) -> Any:
    """Monta o `SqliteDb` do Agno (fallback local, sem AWS).

    Args:
        cfg: Configuração de persistência (usa `sqlite_file`).

    Returns:
        Uma instância de ``agno.db.sqlite.SqliteDb``.
    """
    from agno.db.sqlite import SqliteDb

    return SqliteDb(db_file=cfg.sqlite_file)


def build_db(cfg: PersistenceConfig) -> Any:
    """Constrói o `Db` do Agno selecionado por `cfg.backend`.

    Args:
        cfg: Configuração de persistência já validada por `config.py`.

    Returns:
        O `Db` do Agno correspondente (`DynamoDb` por padrão, ou `SqliteDb`).

    Raises:
        RuntimeError: Se `cfg.backend` não for suportado (defesa em profundidade;
            `config.py` já valida contra o conjunto de backends).
    """
    builders = {
        "dynamodb": _build_dynamodb,
        "sqlite": _build_sqlite,
    }
    builder = builders.get(cfg.backend)
    if builder is None:
        raise RuntimeError(
            "Backend de persistência não suportado no db_factory: "
            "'{}'. Suportados: 'dynamodb', 'sqlite'.".format(cfg.backend)
        )
    return builder(cfg)
