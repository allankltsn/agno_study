# Escopo — Agente 06: Workflow de Processamento de Documentos

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 06
- **Nivel**: 8-9 (workflows basicos + avancados)
- **Skills de referencia**: `agno-workflows-basic`, `agno-workflows-advanced`, `agno-multimodal`
- **Diretorio alvo**: `agents/06-workflow-documentos/`

## 1. Contexto de negocio

Processos de back-office (ex.: onboarding, analise de comprovantes, extracao de dados
de contratos) seguem etapas fixas e nao podem falhar no meio deixando estado
inconsistente. Um humano fazendo isso e lento e sujeito a erro; um agente "conversa"
demais para um processo que deveria ser deterministico. Queremos um workflow com
etapas claras, que retoma de onde parou se falhar e nao duplica trabalho se
reprocessado.

- **Usuario**: sistema/operacao de back-office; auditoria.
- **Dor**: processos manuais frageis, sem garantia de consistencia e auditoria.

## 2. Objetivo do estudo

Dominar Workflows na Agno (Step, ordenacao, e recursos avancados: Condition, Loop,
Router, Parallel), com foco em determinismo, **checkpointing** (retomada) e
**idempotencia** (reprocessar nao duplica).

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve processar um documento numa sequencia de etapas:
  extrair -> validar -> enriquecer -> persistir -> notificar.
- **RF-02**: Cada etapa deve ter entrada/saida bem definidas; a saida de uma alimenta
  a proxima.
- **RF-03**: O sistema deve validar os dados extraidos e desviar (Condition/Router)
  para tratamento quando invalidos.
- **RF-04**: Se o processo falhar numa etapa, deve ser possivel retomar a partir dela
  (checkpoint), sem refazer as anteriores.
- **RF-05**: Reprocessar o mesmo documento (mesmo id) nao deve duplicar o resultado
  (idempotencia).

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Determinismo)**: Mesma entrada produz o mesmo caminho de etapas; sem
  variacao aleatoria no fluxo.
- **RNF-02 (Idempotencia)**: Chave de idempotencia por documento; reexecucao e
  segura.
- **RNF-03 (Auditabilidade)**: Cada etapa registra inicio/fim, entrada/saida (sem
  PII em claro) e status.
- **RNF-04 (Resiliencia)**: Falha isolada em uma etapa nao corrompe o estado; permite
  retomar.
- **RNF-05 (Observabilidade)**: Estado do workflow inspecionavel (qual etapa, qual
  status).

## 5. Fora de escopo

- Roteamento por assunto entre agentes (agente 05).
- Deploy (agentes 07/08).
- OCR de producao real (pode-se simular a extracao; multimodal opcional para PDFs/imagens).

## 6. Premissas e restricoes

- Documento de exemplo com campos conhecidos (ex.: comprovante com valor/data/nome).
- Persistencia local (arquivo/SQLite) para checkpoint e resultado.
- Agno v2 (Workflow, Step, Condition/Loop/Router/Parallel).

## 7. Arquitetura (camadas)

- `domain/models.py`: `Documento`, `DadosExtraidos`, `ResultadoProcessamento`,
  `EstadoWorkflow`.
- `domain/ports.py`: `ExtractorPort`, `ValidatorPort`, `EnricherPort`,
  `ResultRepositoryPort`, `NotifierPort`, `CheckpointPort`.
- `application/workflow/process_document.py`: orquestra as etapas (aplica idempotencia
  e checkpoint).
- `infrastructure/`: adapters de extracao (Agno/multimodal), enriquecimento,
  repositorio de resultado, notificacao, checkpoint.
- `interface/cli/main.py` (processa um doc; permite retomar).
- `composition_root.py`.

Ponto de estudo: idempotencia e checkpoint vivem na orquestracao/infra (chave por
documento + estado persistido), nao nas regras de negocio puras.

## 8. Plano de desenvolvimento

1. Modelar entidades e `EstadoWorkflow`; definir ports de cada etapa.
2. Orquestrador com adapters fake (testar caminho feliz, invalido e retomada).
3. Implementar etapas reais (extrair com Agno, validar, enriquecer).
4. Checkpoint persistido + logica de retomada.
5. Idempotencia por chave de documento.
6. Simular falha na etapa 3 e retomar; reprocessar mesmo doc e verificar nao-duplicacao.

## 9. Criterios de aceite

- [ ] Documento valido percorre todas as etapas e gera resultado (RF-01, RF-02).
- [ ] Documento invalido desvia para tratamento (RF-03).
- [ ] Falha na etapa N permite retomar a partir de N, sem refazer 1..N-1 (RF-04, RNF-04).
- [ ] Reprocessar o mesmo id nao duplica o resultado (RF-05, RNF-02).
- [ ] Estado e etapas auditaveis; sem PII em claro (RNF-03, RNF-05).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar quando usar
workflow vs team, como o checkpoint e a idempotencia foram implementados e por que
o fluxo e deterministico.
