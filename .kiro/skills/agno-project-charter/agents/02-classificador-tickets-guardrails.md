# Escopo — Agente 02: Classificador de Tickets com Guardrails

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 02
- **Nivel**: 1-2 (agentes basicos + tools + saida estruturada)
- **Skills de referencia**: `agno-basic-agents`, `agno-tools`, `agno-agentos-eval-deploy`
- **Diretorio alvo**: `agents/02-classificador-tickets-guardrails/`

## 1. Contexto de negocio

O time de suporte recebe centenas de tickets por dia sem triagem. Um humano precisa
ler cada um, definir urgencia, categoria e o time responsavel antes de encaminhar.
Isso atrasa o atendimento de casos criticos. Queremos um agente que classifique o
ticket automaticamente e produza uma saida estruturada consumida por outro sistema
(fila, roteador, dashboard).

- **Usuario**: sistema de ticketing (consumidor da saida) e o time de operacoes.
- **Dor**: triagem manual lenta, inconsistente e sujeita a erro humano.

## 2. Objetivo do estudo

Aprender **saida estruturada e validada** (schema Pydantic) e **guardrails**:
garantir que o agente devolve sempre um formato valido, que PII nao vaza em logs, e
que decisoes sao auditaveis. Introducao a evals (o classificador nao pode regredir).

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve receber o texto de um ticket e retornar um objeto
  estruturado com: `categoria`, `urgencia` (baixa/media/alta/critica), `time_responsavel`,
  `resumo` e `confianca`.
- **RF-02**: O sistema deve validar a saida contra um schema; se o LLM devolver algo
  invalido, deve reprocessar ou retornar erro tratado (nunca saida malformada).
- **RF-03**: O sistema deve mascarar/anonimizar dados pessoais (email, CPF, telefone)
  antes de gravar qualquer log.
- **RF-04**: O sistema deve registrar, para cada classificacao, a decisao e a
  justificativa (trilha de auditoria), sem PII em claro.
- **RF-05**: O sistema deve expor uma forma de rodar um conjunto de casos de teste
  (eval) comparando saida esperada vs obtida.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Compliance/LGPD)**: Nenhum dado pessoal em claro em logs ou arquivos de
  trace. Politica de mascaramento documentada.
- **RNF-02 (Determinismo)**: `temperature` baixa para classificacao estavel; mesma
  entrada tende a mesma saida.
- **RNF-03 (Auditabilidade)**: Toda decisao rastreavel (id do ticket, versao do
  prompt/modelo, timestamp, resultado).
- **RNF-04 (Qualidade)**: Suite de eval com metrica de acuracia; meta minima
  documentada (ex.: >= 85% no conjunto de teste).
- **RNF-05 (Seguranca)**: Input sanitizado contra prompt injection basico (o texto do
  ticket nao deve conseguir sobrescrever as instrucoes de classificacao).

## 5. Fora de escopo

- Responder ao cliente (isto e do agente 01).
- Rotear de fato para times (o roteamento multi-agente e o agente 05).
- Persistencia em banco real (log em arquivo/estruturado basta aqui).

## 6. Premissas e restricoes

- Conjunto fixo e conhecido de categorias, urgencias e times (enum).
- Ha um pequeno dataset rotulado (10-30 exemplos) para os evals.
- Agno v2 com saida estruturada (schema de resposta).

## 7. Arquitetura (camadas)

- `domain/models.py`: `Ticket`, `Classificacao` (com enums de urgencia/categoria/time).
- `domain/ports.py`: `ClassifierPort`, `PiiMaskerPort`, `AuditLogPort`.
- `application/use_cases/classify_ticket.py`: mascara PII -> classifica -> valida ->
  audita.
- `infrastructure/llm/agno_classifier.py`: adapter Agno com saida estruturada.
- `infrastructure/security/pii_masker.py`: regex/heuristica de mascaramento.
- `infrastructure/audit/file_audit.py`: trilha de auditoria estruturada.
- `interface/cli/main.py` e `interface/eval/run_eval.py`.
- `composition_root.py`.

## 8. Plano de desenvolvimento

1. Modelar enums e `Classificacao` no dominio (schema forte).
2. Caso de uso com PII masker fake e classifier fake (testes primeiro).
3. Adapter Agno com saida estruturada + validacao/reprocesso.
4. PII masker real (email/CPF/telefone) e audit log.
5. Runner de eval com dataset rotulado + calculo de acuracia.
6. Teste de prompt injection (ticket malicioso nao muda a tarefa).

## 9. Criterios de aceite

- [ ] Ticket valido retorna objeto estruturado completo (RF-01).
- [ ] Saida invalida do LLM e tratada (reprocesso ou erro), nunca malformada (RF-02).
- [ ] Logs nunca contem PII em claro (RF-03, RNF-01).
- [ ] Trilha de auditoria registra decisao + versao (RF-04, RNF-03).
- [ ] Eval roda e reporta acuracia >= meta (RF-05, RNF-04).
- [ ] Ticket com texto de "ignore as instrucoes" nao altera o comportamento (RNF-05).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar a estrategia de
guardrails (validacao de schema, mascaramento, anti-injection) e como os evals
protegem contra regressao.
