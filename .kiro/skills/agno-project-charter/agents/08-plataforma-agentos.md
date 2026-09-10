# Escopo — Agente 08: Plataforma AgentOS com Evals e Monitoramento

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 08
- **Nivel**: 10 (avaliacao e AgentOS)
- **Skills de referencia**: `agno-agentos-eval-deploy`, `agno-development-master`, + skills dos agentes reunidos
- **Diretorio alvo**: `agents/08-plataforma-agentos/`

## 1. Contexto de negocio

Depois de varios agentes isolados, a empresa quer uma plataforma unica que sirva
agents, teams e workflows em producao, com um runtime confiavel, monitoramento,
avaliacoes automaticas (para o comportamento nao regredir a cada mudanca de prompt) e
execucao em background para tarefas longas. E o passo que atravessa o "vale da morte"
entre um agente que funciona e um servico de verdade.

- **Usuario**: consumidores dos agentes + time de plataforma que opera e monitora.
- **Dor**: agentes espalhados, sem monitoramento, sem garantia contra regressao.

## 2. Objetivo do estudo

Dominar o AgentOS da Agno (runtime FastAPI stateless, sessoes, tracing, guardrails,
execucao em background, control plane) e os **evals** como rede de seguranca contra
regressao. Reunir 2-3 agentes anteriores sob um unico runtime.

Nota tecnica: o AgentOS e a metade de "runtime de producao" da Agno — um servico
FastAPI pronto para servir agents/teams/workflows, com sessoes, tracing e evals.
Fonte: docs oficiais da Agno (AgentOS). Conteudo reformulado para conformidade.

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve registrar e servir pelo menos dois primitivos (ex.: um
  agent + um team, ou + um workflow) sob o mesmo AgentOS.
- **RF-02**: O sistema deve expor os agentes via a API do AgentOS (endpoints prontos)
  com gestao de sessao.
- **RF-03**: O sistema deve ter uma suite de evals que roda casos de teste e reporta
  metricas por agente.
- **RF-04**: O sistema deve produzir traces/logs observaveis das execucoes.
- **RF-05**: O sistema deve suportar execucao em background para tarefas longas
  (ex.: o workflow do agente 06), sem bloquear a requisicao.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Anti-regressao)**: Evals executaveis em pipeline; mudanca que baixa a
  metrica abaixo do limite falha a entrega.
- **RNF-02 (Observabilidade)**: Tracing ponta a ponta; metricas por agente
  (latencia, sucesso, custo).
- **RNF-03 (Escala/Stateless)**: Runtime stateless; estado (sessoes) em DB externo;
  horizontalmente escalavel.
- **RNF-04 (Guardrails)**: Politicas de guardrail aplicadas (ex.: PII, conteudo);
  reuso dos guardrails do agente 02.
- **RNF-05 (Seguranca/RBAC)**: Acesso multi-usuario controlado; segredos gerenciados.

## 5. Fora de escopo

- Reescrever a logica dos agentes reunidos (eles ja existem).
- Infra de deploy detalhada de nuvem (pode rodar local/EC2 conforme
  `agno-agentos-eval-deploy`; serverless e o agente 07).
- Frontend do control plane (usa-se o disponivel; foco e o backend/plataforma).

## 6. Premissas e restricoes

- Pelo menos dois agentes anteriores prontos (ex.: 01 + 05, ou 03 + 06).
- Datasets de eval de cada agente reunido disponiveis.
- Agno v2 (AgentOS, evals, tracing).

## 7. Arquitetura (camadas + plataforma)

- Cada agente reunido mantem sua arquitetura em camadas (segregada).
- `platform/agentos_app.py`: monta o AgentOS registrando agents/teams/workflows.
- `platform/evals/`: suites de eval por agente + runner agregado.
- `platform/observability/`: configuracao de tracing/metricas.
- `platform/guardrails/`: politicas reaproveitadas (ex.: PII do agente 02).
- `composition_root.py` de plataforma: injeta e registra os primitivos.

Ponto de estudo: a plataforma nao reimplementa os agentes; ela os compoe. Cada agente
continua um projeto com sua propria arquitetura; o AgentOS e a camada de runtime que
os serve juntos.

## 8. Plano de desenvolvimento

1. Escolher 2-3 agentes a reunir e garantir que rodam isolados.
2. Montar o AgentOS registrando esses primitivos; subir a API local.
3. Configurar sessoes em DB externo (stateless).
4. Portar/consolidar suites de eval; criar runner agregado com metricas/limites.
5. Configurar tracing/observabilidade e guardrails.
6. Testar execucao em background (tarefa longa) e o fluxo de eval anti-regressao.

## 9. Criterios de aceite

- [ ] AgentOS serve pelo menos dois primitivos sob a mesma API (RF-01, RF-02).
- [ ] Suite de eval roda e reporta metricas por agente (RF-03).
- [ ] Traces/logs das execucoes sao observaveis (RF-04, RNF-02).
- [ ] Tarefa longa roda em background sem bloquear a requisicao (RF-05).
- [ ] Baixar a metrica de eval abaixo do limite falha a verificacao (RNF-01).
- [ ] Guardrails ativos; runtime stateless com estado externo (RNF-03, RNF-04).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar o papel do
AgentOS como runtime, como os evals previnem regressao, e como a plataforma compoe
agentes sem quebrar a segregacao de cada projeto.
