---
name: agno-project-charter
description: Documento mestre de gestao de projeto (project charter) para o portfolio de 8 agentes Agno de estudo. Define metodologia de levantamento de requisitos, convencoes de escopo e documentacao, criterios de aceite e o roadmap segregado dos agentes. Use ao planejar, escopar, documentar ou revisar QUALQUER um dos 8 agentes do portfolio, ou ao iniciar o desenvolvimento de um deles.
---

# Project Charter — Portfolio de 8 Agentes Agno

Documento mestre de gestao de projeto para o portfolio de estudo de agentes Agno.
Cada agente e um projeto isolado, mas todos compartilham a mesma metodologia de
requisitos, o mesmo padrao de documentacao e a mesma arquitetura em camadas + SOLID.

Este charter e o ponto de entrada. Os escopos detalhados de cada agente vivem em
`agents/<id>-<nome>.md` (dentro desta skill), um por agente.

## 1. Objetivo do portfolio

Formar um profissional capaz de construir agentes Agno prontos para producao em
grandes empresas, aprendendo cada conceito de forma segregada e progressiva, mas
sempre sob restricoes reais de ambiente de trabalho (SLA, custo, seguranca,
observabilidade, compliance, escala).

Fio condutor de negocio (opcional, recomendado): um "assistente interno de uma
fintech" que evolui a cada agente. Isso simula como sistemas reais crescem numa
empresa, em vez de projetos desconectados. Cada agente pode reusar o dominio do
anterior sem acoplar codigo (a arquitetura em camadas garante isso).

## 2. Principio de segregacao

Regra inegociavel: **um agente = um projeto isolado**.

- Cada agente vive em seu proprio diretorio (`agents/<id>-<nome>/`), com
  `pyproject.toml` e `.venv` proprios (via `uv`). Sem venv/imports compartilhados.
- Nenhum agente importa codigo de outro. Se um padrao se repete, ele e copiado e
  adaptado (aprendizado), nao transformado em dependencia compartilhada.
- Cada agente tem seu proprio `README.md`, seus testes e sua documentacao de dev.
- Referencias de skill: `agno-development-master` (principios do workspace) e
  `agno-project-structure` (layout por agente).

## 3. Papeis (RACI simplificado)

Como este e um workspace de estudo com um unico desenvolvedor + agente Kiro,
os papeis sao didaticos, para praticar a disciplina de projeto:

| Papel | Responsabilidade |
|-------|------------------|
| Product Owner (voce) | Prioriza agentes, valida criterios de aceite |
| Tech Lead / Arquiteto | Garante arquitetura em camadas + SOLID |
| Desenvolvedor | Implementa o agente e a documentacao |
| Kiro (assistente) | Escreve escopo, codigo e docs sob orientacao |

## 4. Metodologia de levantamento de requisitos

Para cada agente, o escopo e levantado nesta ordem (do problema para a solucao):

1. **Contexto de negocio** — que problema real de empresa isto resolve? Quem e o
   usuario? Qual a dor?
2. **Objetivo do estudo** — que conceito Agno este agente ensina (o "porque
   tecnico")?
3. **Requisitos funcionais (RF)** — o que o agente DEVE fazer (comportamento
   observavel). Numerados (RF-01, RF-02...).
4. **Requisitos nao-funcionais (RNF)** — restricoes de qualidade: desempenho, custo,
   seguranca, observabilidade, escala, compliance. Numerados (RNF-01...).
5. **Fora de escopo** — o que explicitamente NAO sera feito (evita scope creep).
6. **Premissas e restricoes** — o que assumimos como verdade; limites tecnicos.
7. **Criterios de aceite** — condicoes verificaveis para considerar o agente pronto.

Cada RF/RNF deve ser **testavel**: se nao da para verificar, nao e requisito, e
desejo. Escreva no formato "o sistema deve <acao> para que <valor>".

## 5. "Fatores reais de empresa" (o diferencial deste portfolio)

Tutoriais param no "funciona no notebook". Aqui, cada agente carrega ao menos um
fator de producao real. Use esta lista como checklist ao escopar:

- **SLA e resiliencia**: timeout, retry, fallback de provider, degradacao graciosa.
- **Custo**: tokens logados, modelo certo para a tarefa, evitar chamadas desnecessarias.
- **Seguranca**: segredos fora do codigo, PII protegida em logs, input sanitizado.
- **Compliance**: LGPD/GDPR, auditabilidade das decisoes, retencao de dados.
- **Observabilidade**: logs estruturados, tracing, metricas, evals contra regressao.
- **Escala**: runtime stateless, estado em DB externo, isolamento multi-tenant (RBAC).
- **Reprodutibilidade**: versoes fixas, build reprodutivel, ambientes isolados.

## 6. Padrao de documentacao de desenvolvimento

Cada agente entrega **quatro documentos** (todos obrigatorios):

1. **Escopo/Requisitos** — `agents/<id>-<nome>.md` nesta skill (o "contrato").
2. **README.md** — no diretorio do agente: o que estuda, como rodar, o que observar.
3. **DESENVOLVIMENTO.md** — no diretorio do agente: diario de desenvolvimento
   explicativo (decisoes de design, por que cada camada existe, trade-offs, como a
   arquitetura foi aplicada). Este e o documento "explicativo do desenvolvimento".
4. **Docstrings + comentarios** no codigo — material de estudo, entao o codigo e
   explicito e comentado (nao producao enxuta).

Estrutura recomendada do `DESENVOLVIMENTO.md`:

```
# Desenvolvimento — <Agente>
## Conceito estudado
## Decisoes de arquitetura (por camada)
## Por que cada port/adapter existe
## Trade-offs e alternativas consideradas
## Fatores de producao aplicados (e como)
## Como testar e o que observar
## O que eu faria diferente em producao real
```

## 7. Arquitetura padrao (todos os agentes)

Todos seguem `agno-project-architecture-solid`: camadas ports & adapters
(`domain` <- `application` <- `infrastructure` / `interface`), composicao no
`composition_root`, dominio sem dependencia de framework. A complexidade das
camadas cresce com o nivel do agente (um agente simples pode ter camadas finas;
um team/workflow tera mais adapters). Nunca vaze tipos da Agno para fora da
infraestrutura.

## 8. Definition of Ready (DoR) — antes de codar

- [ ] Escopo do agente escrito e revisado (`agents/<id>-<nome>.md`).
- [ ] RF e RNF numerados e testaveis.
- [ ] Fatores de producao do agente identificados.
- [ ] Skills Agno de referencia listadas.
- [ ] Ambiente isolado planejado (uv, .env.example).

## 9. Definition of Done (DoD) — agente pronto

- [ ] Todos os RF implementados e demonstraveis.
- [ ] RNF atendidos (ou documentado o que ficou pendente e por que).
- [ ] Arquitetura em camadas respeitada (checklist da skill de arquitetura).
- [ ] `README.md` e `DESENVOLVIMENTO.md` completos.
- [ ] Smoke test do `run` passando; testes de dominio sem rede/DB.
- [ ] Segredos fora do repo; `.env.example` presente.
- [ ] Criterios de aceite do escopo verificados um a um.

## 10. Roadmap dos 8 agentes

Ordem sugerida de execucao (dependencia conceitual crescente). Cada linha aponta
para o documento de escopo detalhado.

| ID | Agente | Nivel | Conceito-chave | Fator de producao | Escopo |
|----|--------|-------|----------------|-------------------|--------|
| 01 | Suporte com SLA e Fallback de Provider | 1 | Agent + multi-provider | Resiliencia / SLA | `agents/01-suporte-sla-fallback.md` |
| 02 | Classificador de Tickets com Guardrails | 1-2 | Saida estruturada + tools | Compliance / auditabilidade | `agents/02-classificador-tickets-guardrails.md` |
| 03 | Base de Conhecimento Interna (RAG) | 3 | RAG + citacao de fonte | RBAC / seguranca de docs | `agents/03-base-conhecimento-rag.md` |
| 04 | Conversacional com Memoria Multi-usuario | 4 | Sessions + persistencia | Escala / isolamento tenant | `agents/04-conversacional-memoria.md` |
| 05 | Team de Roteamento de Atendimento | 7 | Teams + delegacao | Custo / divisao por dominio | `agents/05-team-roteamento.md` |
| 06 | Workflow de Processamento de Documentos | 8-9 | Workflows deterministicos | Idempotencia / checkpoint | `agents/06-workflow-documentos.md` |
| 07 | API Serverless de Agente (Lambda+SAM) | 11 | Deploy serverless | Ambientes isolados / IaC | `agents/07-api-serverless.md` |
| 08 | Plataforma AgentOS com Evals | 10 | AgentOS + evals | Monitoramento / anti-regressao | `agents/08-plataforma-agentos.md` |

Trajetoria recomendada de estudo: 01 -> 03 -> 04 -> 05 -> 07 -> 08, encaixando 02 e
06 quando o tema (guardrails / workflows) for prioridade. Os agentes 07 e 08 podem
"vestir" um agente anterior em vez de criar dominio novo.

## 11. Como usar esta skill

- Ao **iniciar** um agente: leia este charter + o `agents/<id>-<nome>.md` dele.
  Confirme a DoR antes de codar.
- Ao **documentar**: siga a secao 6 (quatro documentos obrigatorios).
- Ao **revisar/entregar**: rode a DoD (secao 9) e os criterios de aceite do escopo.
- Ao **escopar um novo agente** fora da lista: copie o template da secao 4 e crie um
  novo `agents/<id>-<nome>.md`.
