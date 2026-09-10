# Escopo — Agente 05: Team de Roteamento de Atendimento

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 05
- **Nivel**: 7 (teams multi-agente)
- **Skills de referencia**: `agno-teams`, `agno-reasoning`, `agno-project-architecture-solid`
- **Diretorio alvo**: `agents/05-team-roteamento/`

## 1. Contexto de negocio

O atendimento cobre assuntos muito diferentes (financeiro, tecnico, juridico). Um
unico agente generalista responde mal fora de seu dominio e carrega instrucoes/tools
de todas as areas ao mesmo tempo (caro e confuso). Queremos um coordenador que
entenda a solicitacao e a encaminhe ao especialista certo, como um time real com
areas de responsabilidade.

- **Usuario**: cliente/funcionario que faz uma solicitacao de assunto variado.
- **Dor**: respostas rasas de generalista; custo de acionar tudo para tudo.

## 2. Objetivo do estudo

Dominar Teams na Agno (lider/coordenador, delegacao a especialistas), aplicando
divisao por dominio de negocio e controle de custo (aciona so o especialista
relevante). Praticar reasoning para a decisao de roteamento.

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve ter um agente coordenador que classifica a solicitacao e
  delega ao especialista adequado (financeiro, tecnico ou juridico).
- **RF-02**: Cada especialista deve ter instructions e tools proprias do seu dominio.
- **RF-03**: O coordenador deve acionar apenas o(s) especialista(s) necessario(s),
  nao todos.
- **RF-04**: O sistema deve retornar a resposta do especialista identificando qual
  area respondeu.
- **RF-05**: Se a solicitacao nao se encaixar em nenhuma area, o sistema deve
  responder com encaminhamento a humano/area default.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Custo)**: Cada solicitacao aciona o minimo de especialistas; log de quais
  agentes foram chamados por requisicao.
- **RNF-02 (Observabilidade)**: Trace da decisao de roteamento (por que foi para tal
  area) e da resposta final.
- **RNF-03 (Extensibilidade/OCP)**: Adicionar uma nova area (ex.: "cobranca") deve
  ser adicionar um especialista, sem reescrever o coordenador.
- **RNF-04 (Isolamento de dominio)**: Tools de uma area nao sao expostas a outra.
- **RNF-05 (Resiliencia)**: Falha de um especialista nao derruba o time; retorna erro
  tratado para aquela area.

## 5. Fora de escopo

- Persistencia de memoria entre sessoes (agente 04) — pode reusar conceito, mas nao
  e o foco.
- Deploy (agentes 07/08).
- Workflows deterministicos com etapas fixas (agente 06) — aqui a orquestracao e por
  roteamento, nao por processo linear.

## 6. Premissas e restricoes

- Tres especialistas iniciais: financeiro, tecnico, juridico (+ default).
- Tools simuladas por area (ex.: consulta de saldo fake, base tecnica fake).
- Agno v2 (Team, delegacao).

## 7. Arquitetura (camadas)

- `domain/models.py`: `Solicitacao`, `Area`, `RespostaEspecialista`.
- `domain/ports.py`: `RouterPort`, `EspecialistaPort` (contrato comum dos
  especialistas), `LLMPort`.
- `application/use_cases/handle_request.py`: usa o router para escolher e delegar.
- `infrastructure/team/agno_team.py`: monta o Team Agno (coordenador + especialistas).
- `infrastructure/specialists/`: adapters por area (cada um implementa `EspecialistaPort`).
- `interface/cli/main.py`.
- `composition_root.py` (registra os especialistas disponiveis).

Ponto de estudo: adicionar area = registrar novo `EspecialistaPort` no composition
root; o caso de uso e o coordenador dependem da abstracao (OCP + DIP).

## 8. Plano de desenvolvimento

1. Modelar `Solicitacao`/`Area`/`RespostaEspecialista` e as ports.
2. Caso de uso `HandleRequest` com router fake e especialistas fake (testes de
   roteamento e "aciona so o necessario" primeiro).
3. Especialistas reais (Agno agents) com tools proprias por dominio.
4. Coordenador/Team Agno fazendo a delegacao.
5. Caminho default (sem area) e resiliencia (especialista que falha).
6. Log de agentes acionados por requisicao.

## 9. Criterios de aceite

- [ ] Solicitacao financeira vai ao especialista financeiro, e assim por diante (RF-01).
- [ ] Apenas o especialista relevante e acionado; log confirma (RF-03, RNF-01).
- [ ] Resposta identifica a area que respondeu (RF-04).
- [ ] Solicitacao ambigua/sem area cai no default (RF-05).
- [ ] Adicionar "cobranca" nao altera o coordenador (RF-02, RNF-03).
- [ ] Falha de um especialista retorna erro tratado, sem derrubar o time (RNF-05).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar a diferenca
entre team (roteamento) e workflow (processo), como o custo e controlado e como novos
especialistas entram sem tocar no coordenador.
