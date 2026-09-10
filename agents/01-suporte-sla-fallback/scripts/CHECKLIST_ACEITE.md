# Checklist de aceite — Agente 01 (Suporte com SLA e Fallback)

Este checklist é o **roteiro de aceite** do agente, mapeado ao escopo
§9 (`Critérios de aceite`) do contrato do agente
(`.kiro/skills/agno-project-charter/agents/01-suporte-sla-fallback.md`) e aos
requisitos correspondentes (`requirements.md`).

Cada item pode ser verificado com o script executável `scripts/smoke_test.py`:

```bash
# Determinístico — sem rede/sem chaves (seguro em qualquer máquina):
python scripts/smoke_test.py

# Ao vivo — requer MODEL_PROVIDER_ORDER + chaves reais dos providers:
python scripts/smoke_test.py --live
#   ou:  SMOKE_LIVE=1 python scripts/smoke_test.py --live
```

> O smoke test **não faz parte do gate de PR** (não é coletado pelo `pytest`).
> É uma ferramenta de verificação manual/demonstração. O modo determinístico
> reproduz todos os cenários de comportamento e de log sem chaves e sem rede; o
> modo `--live` exige providers reais e é reservado para verificação ponta a ponta.

---

## Mapa: escopo §9 → cenário do smoke → requisito

| # | Critério de aceite (§9) | Cenário no smoke | Requisitos | Como verificar / o que observar |
|---|-------------------------|------------------|------------|----------------------------------|
| §9.1 | Pergunta conhecida retorna resposta coerente | Cenário 1 (modo `run`) | RF-01 / Req 1.1, 1.2 | Resposta fundamentada na doc; `out_of_scope=False`, `escalated=False`, `text` não vazio. |
| §9.2 | `run` e `stream` funcionam | Cenários 1 e 2 | RF-02 / Req 2.1, 2.3, 2.5 | `run` retorna um `AnswerResponse`; `stream` emite fragmentos na ordem; a concatenação do stream é consistente com o `text` do `run`. |
| §9.3 | Pergunta fora de escopo retorna encaminhamento, não alucinação | Cenário 3 | RF-03 / Req 3.2, 3.3, 3.4 | Mensagem de encaminhamento a humano; `out_of_scope=True`, `escalated=True`; o texto não fundamentado do modelo **não** aparece. |
| §9.4 | Trocar provider primário via env não exige mudança de código | (env — ver abaixo) | RF-04 / Req 4.1, 4.2 | Alterar `MODEL_PROVIDER_ORDER` muda a ordem de fallback sem tocar `domain/` nem `application/` (validado também por `tests/.../test_composition_root.py`). |
| §9.5 | Derrubando o primário (chave inválida/timeout forçado), o secundário responde e o log registra o fallback | Cenário 4 | RF-05, RNF-02 / Req 5.2, 5.3, 5.4, 9.1 | Provider que responde = secundário; log estruturado com `used_fallback=true` e `attempts=[primário, secundário]`. |
| §9.6 | Log estruturado mostra provider, latência e tokens | Cenário 5 | RNF-03, RNF-05 / Req 8.3, 9.1, 9.2 | Uma linha JSON por requisição com `provider`, `latency_ms`, `prompt_tokens`/`completion_tokens`/`total_tokens`, `used_fallback`, `attempts`, `result`; a pergunta aparece só como `question_hash`. |

---

## Checklist (marque ao validar)

- [ ] **§9.1 — Pergunta conhecida → resposta coerente** (RF-01)
  - Rodar Cenário 1. Observar resposta fundamentada, `out_of_scope=False`, `escalated=False`.

- [ ] **§9.2 — `run` e `stream` funcionam** (RF-02)
  - Rodar Cenários 1 e 2. Confirmar que o modo `stream` entrega fragmentos na ordem e que a
    concatenação é consistente com o texto do modo `run`.

- [ ] **§9.3 — Fora de escopo → encaminhamento, não alucinação** (RF-03)
  - Rodar Cenário 3. Confirmar mensagem de encaminhamento, `out_of_scope=True`,
    `escalated=True`, e ausência de texto não fundamentado do modelo.

- [ ] **§9.4 — Trocar provider primário via env, sem mudar código** (RF-04)
  - Ajustar `MODEL_PROVIDER_ORDER` (ex.: de `openai,groq` para `groq,openai`) e confirmar
    que o primário muda **sem** editar código de domínio/aplicação. No modo `--live`,
    observar o campo `provider` da resposta refletir a nova ordem.

- [ ] **§9.5 — Primário derrubado → secundário responde e log registra o fallback**
      (RF-05, RNF-02)
  - Determinístico: rodar Cenário 4 (primário sempre falha) e conferir
    `used_fallback=true` e `attempts` na ordem correta no log.
  - Ao vivo: exportar uma chave inválida para o primário (ou reduzir
    `LLM_PER_ATTEMPT_TIMEOUT`) e observar o secundário responder no log.

- [ ] **§9.6 — Log estruturado mostra provider, latência e tokens** (RNF-03, RNF-05)
  - Rodar Cenário 5. Conferir a linha JSON com `provider`, `latency_ms`, tokens
    (`prompt`/`completion`/`total`), `used_fallback`, `attempts`, `result="ok"`, e a
    pergunta apenas como `question_hash` (sem PII, sem chave de API).

---

## Notas

- O modo determinístico monta o `FallbackLLM` real com dublês de `LLMPort` em processo
  (nenhum tipo da Agno, nenhuma rede), de modo que a lógica de fallback e o log estruturado
  são os **mesmos** exercitados em produção — só a fonte dos providers muda.
- O modo `--live` usa o `composition_root` (mesmo caminho de montagem da CLI e da API),
  batendo nos providers reais definidos por `MODEL_PROVIDER_ORDER`.
- Segredos: em nenhum modo o script imprime chaves de API; a pergunta nunca é logada em
  claro (só como `question_hash`).
