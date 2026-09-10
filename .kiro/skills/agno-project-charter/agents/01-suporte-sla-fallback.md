# Escopo — Agente 01: Suporte com SLA e Fallback de Provider

> Documento de escopo e requisitos. Contrato do agente. Ler junto ao `SKILL.md`
> (charter). Ao concluir, gerar `README.md` e `DESENVOLVIMENTO.md` no diretorio do agente.

- **ID**: 01
- **Nivel**: 1 (agentes basicos)
- **Skills de referencia**: `agno-basic-agents`, `agno-tools`, `agno-project-architecture-solid`
- **Diretorio alvo**: `agents/01-suporte-sla-fallback/`

## 1. Contexto de negocio

Uma empresa oferece um produto (ex.: o "app da fintech") e recebe muitas duvidas
repetidas de clientes (FAQ, como usar recursos, erros comuns). O time de suporte
humano nao escala e o custo por atendimento e alto. Queremos um agente de primeiro
nivel que responda duvidas com base na documentacao do produto, com qualidade
consistente e tempo de resposta previsivel.

- **Usuario**: cliente final (via chat) e, indiretamente, o time de suporte.
- **Dor**: volume alto de duvidas simples, tempo de espera, custo de atendimento.

## 2. Objetivo do estudo

Dominar o agente Agno basico (persona, instructions, run vs stream, parametros de
sampling) e, ao mesmo tempo, aprender a torna-lo resiliente: **multi-provider com
fallback** por tras de uma unica `LLMPort`. E o exercicio pratico do principio
Aberto/Fechado (OCP) e Inversao de Dependencia (DIP).

## 3. Requisitos funcionais (RF)

- **RF-01**: O sistema deve receber uma pergunta em texto e retornar uma resposta
  fundamentada na documentacao/contexto do produto.
- **RF-02**: O sistema deve suportar modo `run` (resposta completa) e `stream`
  (resposta incremental) para que a interface escolha a melhor UX.
- **RF-03**: O sistema deve responder "nao sei / vou encaminhar para um humano"
  quando a pergunta estiver fora do escopo conhecido, em vez de inventar.
- **RF-04**: O sistema deve selecionar o provider de LLM por configuracao (env),
  sem alterar codigo de dominio.
- **RF-05**: Em caso de falha/timeout do provider primario, o sistema deve tentar
  automaticamente um provider secundario (fallback) e registrar o evento.

## 4. Requisitos nao-funcionais (RNF)

- **RNF-01 (SLA)**: Cada tentativa de chamada ao LLM deve ter timeout configuravel
  (padrao sugerido: 10s). O tempo total (com fallback) deve ter limite superior.
- **RNF-02 (Resiliencia)**: Retry com no maximo N tentativas por provider (padrao 2)
  com backoff; apos esgotar, cai no fallback; se todos falharem, degradacao graciosa
  (mensagem clara ao usuario, sem stacktrace).
- **RNF-03 (Custo)**: Registrar tokens consumidos e provider usado por requisicao em
  log estruturado, para permitir analise de custo.
- **RNF-04 (Seguranca)**: Chaves de API somente via `.env`/variavel de ambiente,
  nunca no codigo. `.env` no `.gitignore`.
- **RNF-05 (Observabilidade)**: Log estruturado por requisicao com: pergunta (ou hash),
  provider, latencia, tokens, se houve fallback, resultado (ok/erro).

## 5. Fora de escopo

- RAG / busca em base vetorial (fica no agente 03).
- Memoria entre sessoes (fica no agente 04).
- Deploy em nuvem (fica nos agentes 07/08).
- Interface grafica; a entrega e CLI + funcao chamavel.

## 6. Premissas e restricoes

- Ao menos dois providers configurados (ex.: OpenAI + Groq, ou Groq + Ollama local).
- Documentacao do produto cabe no contexto/instructions (sem RAG ainda).
- Python 3.9+; Agno v2; gerenciamento com `uv`.

## 7. Arquitetura (camadas)

- `domain/ports.py`: `LLMPort` (metodo `ask(prompt) -> Answer`), `Answer` value object.
- `domain/exceptions.py`: `ProviderUnavailable`, `OutOfScopeQuestion`.
- `application/use_cases/answer_question.py`: orquestra; recebe `LLMPort` injetada.
- `infrastructure/llm/`: `openai_adapter.py`, `groq_adapter.py`, e um
  `fallback_llm.py` (compoe varios adapters e implementa a mesma `LLMPort`).
- `infrastructure/config.py`: le env (providers, timeouts, retries).
- `interface/cli/main.py`: recebe pergunta, chama caso de uso, formata saida.
- `composition_root.py`: monta o `FallbackLLM([primary, secondary])` e injeta.

Ponto-chave de estudo: o caso de uso NAO sabe que existe fallback. Para ele, ha uma
`LLMPort`. O fallback e um adapter que implementa a port e por dentro tenta varios
providers (padrao Composite + Strategy).

## 8. Plano de desenvolvimento

1. Definir `Answer`, `LLMPort` e excecoes no dominio.
2. Escrever o caso de uso `AnswerQuestion` com testes usando um fake da `LLMPort`.
3. Implementar `OpenAIAdapter` e `GroqAdapter` (cada um envolve o `Agent` da Agno).
4. Implementar `FallbackLLM` (timeout + retry + fallback + log).
5. Montar `composition_root` e a CLI.
6. Smoke test: pergunta valida, pergunta fora de escopo, simular falha do primario.

## 9. Criterios de aceite

- [ ] Pergunta conhecida retorna resposta coerente (RF-01).
- [ ] `run` e `stream` funcionam (RF-02).
- [ ] Pergunta fora de escopo retorna encaminhamento, nao alucinacao (RF-03).
- [ ] Trocar provider primario via env nao exige mudanca de codigo (RF-04).
- [ ] Derrubando o primario (chave invalida/timeout forcado), o secundario responde
      e o log registra o fallback (RF-05, RNF-02).
- [ ] Log estruturado mostra provider, latencia e tokens (RNF-03, RNF-05).

## 10. Definition of Done

Ver DoD no charter (secao 9) + criterios acima. Entregar `README.md` e
`DESENVOLVIMENTO.md` explicando por que o fallback vive na infraestrutura e nao no
caso de uso.
