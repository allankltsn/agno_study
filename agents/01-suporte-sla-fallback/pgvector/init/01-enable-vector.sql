-- Habilita a extensão pgvector de forma idempotente no primeiro boot do banco.
-- A imagem `pgvector/pgvector` já traz a extensão compilada; aqui apenas a
-- registramos no banco padrão (POSTGRES_DB). O Agno cria a tabela de vetores
-- (RAG_TABLE_NAME) sozinho no primeiro carregamento do Knowledge.
CREATE EXTENSION IF NOT EXISTS vector;
