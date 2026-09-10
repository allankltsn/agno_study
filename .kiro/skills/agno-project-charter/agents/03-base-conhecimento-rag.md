# Escopo — Agente 03: Base de Conhecimento Interna (RAG)

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 03
- **Nivel**: 3 (conhecimento e RAG)
- **Skills de referencia**: `agno-knowledge-rag`, `agno-memory-sessions`, `agno-project-architecture-solid`
- **Diretorio alvo**: `agents/03-base-conhecimento-rag/`

## 1. Contexto de negocio

Funcionarios perdem tempo procurando informacao em politicas internas, runbooks e
documentacao tecnica espalhada. Respostas erradas por informacao desatualizada geram
risco. Queremos um assistente que responda com base na documentacao interna oficial,
**sempre citando a fonte**, e que respeite quem pode ver o que (uma pessoa de suporte
nao ve documentos de RH restritos).

- **Usuario**: funcionario interno (varias areas com permissoes diferentes).
- **Dor**: informacao dispersa, desatualizada e risco de acesso indevido.

## 2. Objetivo do estudo

Dominar RAG na Agno (Knowledge, embeddings, vector DB, ingestao de documentos) e
adicionar duas exigencias de producao: **citacao obrigatoria da fonte** e **controle
de acesso por permissao (RBAC)** na recuperacao.

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve ingerir documentos (ex.: markdown/PDF) numa base vetorial,
  com metadados (titulo, area, nivel de acesso, versao).
- **RF-02**: O sistema deve responder perguntas recuperando trechos relevantes e
  citando a(s) fonte(s) usada(s) (titulo + referencia).
- **RF-03**: Se nenhum trecho relevante for encontrado, o sistema deve dizer que nao
  encontrou base para responder (sem alucinar).
- **RF-04**: O sistema deve filtrar a recuperacao pela permissao do usuario: so
  recupera documentos que o `perfil` do usuario pode acessar.
- **RF-05**: O sistema deve permitir reindexar quando a documentacao mudar (nova
  versao), sem duplicar conteudo antigo.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Rastreabilidade)**: Toda resposta traz de onde veio (fonte + versao do
  documento). Resposta sem fonte e considerada falha.
- **RNF-02 (Seguranca/RBAC)**: Documento restrito nunca aparece para perfil sem
  permissao, nem mesmo como trecho recuperado.
- **RNF-03 (Qualidade da recuperacao)**: Parametros de chunking e top-k documentados
  e ajustaveis; avaliar relevancia com um pequeno conjunto de perguntas de teste.
- **RNF-04 (Custo)**: Embeddings gerados uma vez por versao de documento (nao a cada
  pergunta); log de tokens de embedding e de resposta.
- **RNF-05 (Versionamento)**: Reindexacao idempotente por versao do documento.

## 5. Fora de escopo

- Memoria conversacional entre sessoes (agente 04).
- Interface web; entrega e CLI + funcao chamavel.
- Escrita/edicao de documentos (somente leitura/consulta).

## 6. Premissas e restricoes

- Conjunto inicial de documentos de exemplo com niveis de acesso simulados.
- Vector DB local para estudo (ex.: LanceDb); trocavel por gerenciado depois.
- Perfis de acesso simplificados (ex.: `suporte`, `engenharia`, `rh`, `admin`).

## 7. Arquitetura (camadas)

- `domain/models.py`: `Documento`, `Trecho`, `Fonte`, `PerfilAcesso`.
- `domain/ports.py`: `KnowledgeRetrieverPort` (busca filtrada por perfil),
  `IngestorPort`, `LLMPort`.
- `application/use_cases/answer_from_knowledge.py`: recupera (com filtro RBAC) ->
  monta contexto -> gera resposta com citacao.
- `application/use_cases/ingest_documents.py`: ingestao/reindexacao versionada.
- `infrastructure/knowledge/agno_knowledge.py`: adapter Agno Knowledge + vector DB.
- `infrastructure/llm/agno_agent.py`: geracao da resposta.
- `interface/cli/main.py`.
- `composition_root.py`.

Ponto de estudo: o filtro RBAC acontece na recuperacao (na port/adapter), nao depois
da resposta. O caso de uso pede "trechos que ESTE perfil pode ver".

## 8. Plano de desenvolvimento

1. Modelar `Documento`/`Trecho`/`Fonte`/`PerfilAcesso` e as ports.
2. Caso de uso de resposta com retriever fake (testes de citacao e RBAC primeiro).
3. Ingestao com metadados e versionamento (adapter Agno + vector DB).
4. Retriever real com filtro por perfil.
5. Geracao de resposta com citacao obrigatoria.
6. Conjunto de perguntas de teste para avaliar relevancia e RBAC.

## 9. Criterios de aceite

- [ ] Ingestao cria a base com metadados de acesso e versao (RF-01).
- [ ] Resposta cita fonte(s); sem fonte relevante, informa que nao encontrou (RF-02, RF-03, RNF-01).
- [ ] Perfil `suporte` nunca recebe trecho de documento `rh` restrito (RF-04, RNF-02).
- [ ] Reindexar nova versao nao duplica conteudo antigo (RF-05, RNF-05).
- [ ] Embeddings gerados por versao, nao por pergunta (RNF-04).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar chunking, top-k,
como o RBAC entra na recuperacao e por que a citacao e obrigatoria.
