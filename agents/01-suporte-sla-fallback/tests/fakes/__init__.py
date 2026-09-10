"""Fakes reutilizáveis da camada de transporte determinístico (Req 17.2 / 17.3).

Este pacote consolida os duplos de teste que, antes, viviam espalhados e
duplicados pelos módulos de `tests/` (por exemplo `AlwaysOk`/`AlwaysFails` em
`test_request_id_correlation.py`, `FakeClock`/`ProgrammableAdapter` em
`test_fallback_llm_backoff.py`, o `FakeLLM` de `test_answer_question.py`). Reuni-los
aqui evita divergência entre cópias e cumpre o item de design "Fakes reutilizáveis
(Req 17.2) — `tests/fakes/`".

Todos os fakes:

- Implementam (estruturalmente) a `LLMPort` do domínio — têm `ask` e `ask_stream`.
- **Não realizam acesso à rede** nem `sleep` de parede: são 100% determinísticos e
  compõem a camada de transporte que serve de gate obrigatório de cada mudança
  (Req 17.1). O tempo, quando relevante, é simulado pelo `FakeClock`.

Exports:

- `FakeLLM`: retorna um `Answer` fixo (ou `out_of_scope=True`), ou levanta uma
  exceção configurada. Base da camada 1 e do teste de `AnswerQuestion`.
- `ProgrammableAdapter`: falha um número configurável de vezes antes de responder
  (ou falha sempre); conta chamadas; aceita `delay_s` (tempo virtual por chamada).
- `FakeClock`: relógio controlável (`advance(seconds)`), compatível com
  `clock=time.monotonic`, para testar timeout/backoff/SLA sem `sleep` real (Req 17.3).
- `AlwaysOk`: `LLMPort` que sempre responde com sucesso e no ato.
- `AlwaysFails`: `LLMPort` que sempre falha com `ProviderUnavailable`.
- `SlowProvider`: `LLMPort` que consome tempo virtual (avança o `FakeClock`) antes
  de responder ou falhar — exercita o SLA total e o timeout por tentativa.
"""

from __future__ import annotations

from tests.fakes.llm import (
    AlwaysFails,
    AlwaysOk,
    FakeClock,
    FakeLLM,
    ProgrammableAdapter,
    SlowProvider,
)

__all__ = [
    "AlwaysFails",
    "AlwaysOk",
    "FakeClock",
    "FakeLLM",
    "ProgrammableAdapter",
    "SlowProvider",
]
