# Escopo — Agente 04: Conversacional com Memoria Multi-usuario

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md` (charter).

- **ID**: 04
- **Nivel**: 4 (memoria e persistencia)
- **Skills de referencia**: `agno-memory-sessions`, `agno-project-architecture-solid`, `agno-lambda-apigateway`
- **Diretorio alvo**: `agents/04-conversacional-memoria/`

## 1. Contexto de negocio

Um assistente que "esquece" tudo a cada mensagem frustra o usuario e obriga a
repetir contexto. Numa empresa com muitos usuarios simultaneos, o assistente precisa
lembrar o historico de cada pessoa, mantendo isolamento total entre usuarios (o
contexto de um nunca vaza para outro) e sem guardar estado no processo (para escalar
horizontalmente).

- **Usuario**: qualquer usuario recorrente do assistente.
- **Dor**: perda de contexto entre sessoes; risco de vazamento entre usuarios.

## 2. Objetivo do estudo

Dominar sessoes, memoria e persistencia na Agno, aplicando o padrao da skill de
arquitetura: **sessions atras de uma port de repositorio** — SQLite no estudo,
DynamoDB em producao — sem tocar no dominio. Aprender runtime stateless + estado
externo (base para escala e serverless).

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve manter historico de conversa por `user_id` + `session_id`
  e usa-lo como contexto nas proximas mensagens.
- **RF-02**: O sistema deve iniciar uma nova sessao e retomar uma sessao existente.
- **RF-03**: O sistema deve isolar dados por usuario: uma sessao so e acessivel pelo
  usuario dono.
- **RF-04**: O sistema deve permitir trocar o backend de persistencia (SQLite <->
  DynamoDB) por configuracao, sem alterar dominio/casos de uso.
- **RF-05**: O sistema deve permitir limpar/expirar sessoes (retencao de dados).

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (Escala/Stateless)**: Nenhum estado de conversa mantido em memoria do
  processo entre requisicoes; tudo vai para o repositorio externo.
- **RNF-02 (Isolamento multi-tenant)**: Acesso a sessao verifica dono; tentativa de
  ler sessao de outro usuario e negada e registrada.
- **RNF-03 (Privacidade/Retencao)**: Politica de retencao configuravel; possibilidade
  de apagar dados de um usuario (LGPD — direito ao esquecimento).
- **RNF-04 (Desempenho)**: Recuperar contexto de sessao com custo previsivel (chave
  por user/session; sem varredura).
- **RNF-05 (Portabilidade)**: Trocar o backend nao muda o comportamento observavel
  (mesmo contrato — LSP).

## 5. Fora de escopo

- RAG (agente 03) — aqui o foco e memoria, nao conhecimento externo.
- Deploy real (agentes 07/08), embora a arquitetura ja prepare para isso.
- Autenticacao real de usuario (o `user_id` vem da interface; auth e simulada).

## 6. Premissas e restricoes

- `user_id` fornecido pela interface (auth assumida como resolvida).
- SQLite para estudo local; DynamoDB apenas como segundo adapter (pode ser mock/local).
- Agno v2 (Sessions/DB).

## 7. Arquitetura (camadas)

- `domain/models.py`: `Mensagem`, `Sessao` (com `user_id`, `session_id`).
- `domain/ports.py`: `SessionReaderPort`, `SessionWriterPort` (ISP — ler e escrever
  separados), `LLMPort`.
- `application/use_cases/chat.py`: carrega sessao (do usuario) -> gera resposta ->
  persiste turno.
- `application/use_cases/purge_user_data.py`: retencao/esquecimento.
- `infrastructure/persistence/sqlite_repo.py` e `dynamo_repo.py` (mesmas ports).
- `interface/cli/main.py` (recebe user_id/session_id).
- `composition_root.py` (escolhe o backend por env).

## 8. Plano de desenvolvimento

1. Modelar `Sessao`/`Mensagem` e as ports de leitura/escrita.
2. Caso de uso `Chat` com repositorio fake em memoria (testes de isolamento primeiro).
3. Adapter SQLite implementando as ports.
4. Adapter DynamoDB (mesmo contrato) para provar portabilidade.
5. Caso de uso de purga/retencao.
6. Teste de isolamento: usuario A nao acessa sessao de B.

## 9. Criterios de aceite

- [ ] Assistente lembra o contexto dentro da mesma sessao (RF-01).
- [ ] Nova sessao e retomada de sessao existente funcionam (RF-02).
- [ ] Usuario A recebe erro ao tentar acessar sessao de B, e o evento e logado (RF-03, RNF-02).
- [ ] Trocar SQLite -> DynamoDB por env mantem o comportamento (RF-04, RNF-05).
- [ ] Purga remove os dados do usuario (RF-05, RNF-03).
- [ ] Nenhum estado de conversa fica no processo entre requisicoes (RNF-01).

## 10. Definition of Done

DoD do charter + criterios acima. `DESENVOLVIMENTO.md` deve explicar por que o estado
vive fora do processo (stateless -> escala) e como as ports de leitura/escrita
aplicam ISP e permitem trocar o backend (LSP/DIP).
