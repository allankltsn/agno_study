"""Property-based tests do `FallbackLLM` — Property 1: Ordem de fallback.

Este módulo cobre a **Property 1 (Ordem de fallback)** do design (Validates:
Requirements 5.1): dado um `FallbackLLM` cujo **primeiro** provider responde
dentro do SLA, `ask(prompt)` deve retornar a resposta desse provider primário —
com `Answer.provider` igual ao nome do primário — e `used_fallback = false`
(tanto no log estruturado quanto na ausência de fallback observável).

Estratégia (por que property-based):
- Em vez de fixar um único prompt/nome/token, usamos `hypothesis` para gerar uma
  ampla variedade de prompts, nomes de provider e contagens de tokens. A
  propriedade — "se o primário responde no SLA, ele vence e não há fallback" —
  precisa valer para **todas** essas entradas (quantificação universal).
- Sem rede e sem `sleep` real: o `FallbackLLM` recebe um `clock`/`sleep`
  injetáveis. Aqui o primário responde imediatamente, então não há backoff a
  exercitar; usamos os defaults só para manter o teste determinístico e rápido.

Fakes locais (intencional): por ora, os duplos (`AlwaysOk` e um logger que grava
as chamadas em memória) ficam **dentro deste módulo**, autocontidos, para não
colidir com as tarefas 8.3/8.4/8.5 que rodam em paralelo. A consolidação de
fakes reutilizáveis em `tests/fakes/` é responsabilidade da tarefa 22.1.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List

from hypothesis import given, settings
from hypothesis import strategies as st

from suporte.domain.ports import Answer
from suporte.infrastructure.llm.fallback_llm import FallbackLLM


# --------------------------------------------------------------------- fakes ---
class AlwaysOk:
    """`LLMPort` fake que responde **sempre** com sucesso e no ato (dentro do SLA).

    Devolve um `Answer` fixo com o `provider` igual ao seu próprio `name`, de modo
    que possamos verificar que o `FallbackLLM` propaga o provider vencedor. Expõe
    o atributo `name` para que `FallbackLLM` resolva o nome do provider antes de
    haver uma `Answer` (usado em `attempts`/log).

    Args:
        name: Nome do provider (aparece em `Answer.provider` e em `attempts`).
        text: Texto da resposta.
        prompt_tokens: Tokens de entrada reportados na `Answer`.
        completion_tokens: Tokens de saída reportados na `Answer`.
    """

    def __init__(
        self,
        name: str,
        text: str = "resposta",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        self.name = name
        self._text = text
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text=self._text,
            provider=self.name,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield self._text


class RecordingLogger:
    """Logger em memória que grava cada chamada a `log_request` como um dict.

    Serve como duplo da dependência `logger` esperada pelo `FallbackLLM`
    (mesma assinatura keyword-only de `StructuredLogger.log_request`). Guardamos
    as chamadas em `self.calls` para asserir, na propriedade, que o registro de
    sucesso saiu com `used_fallback=False` e `result="ok"`.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def log_request(
        self,
        *,
        question_hash: str,
        provider: str,
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        used_fallback: bool,
        attempts: List[str],
        result: str,
        request_id: Any = None,
    ) -> None:
        self.calls.append(
            {
                "question_hash": question_hash,
                "provider": provider,
                "latency_ms": latency_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "used_fallback": used_fallback,
                "attempts": list(attempts),
                "result": result,
                "request_id": request_id,
            }
        )


# ------------------------------------------------------------- estratégias -----
# Nomes de provider não vazios e sem caracteres de controle (rótulos legíveis).
provider_names = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=20,
)
# Prompts arbitrários (podem ser vazios): o conteúdo não deve alterar a ordem.
prompts = st.text(max_size=200)
# Contagens de tokens não-negativas e realistas para o teste.
token_counts = st.integers(min_value=0, max_value=10_000)


# --------------------------------------------------------------- Property 1 ----
class TestProperty1OrdemDeFallback:
    """Property 1: se o primário responde no SLA, ele vence e não há fallback.

    **Validates: Requirements 5.1**
    """

    @settings(max_examples=200)
    @given(
        prompt=prompts,
        primary_name=provider_names,
        secondary_name=provider_names,
        prompt_tokens=token_counts,
        completion_tokens=token_counts,
    )
    def test_primario_responde_no_sla_vence_sem_fallback(
        self,
        prompt: str,
        primary_name: str,
        secondary_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        # Primário é um AlwaysOk que responde imediatamente (dentro do SLA). O
        # secundário existe para provar que ele NÃO é usado quando o primário
        # já responde. Nomes distintos garantem que a asserção sobre `provider`
        # não passe por coincidência.
        primary = AlwaysOk(
            name=primary_name,
            text="ok",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        secondary = AlwaysOk(name=secondary_name + "_secundario", text="fallback")
        logger = RecordingLogger()

        fallback = FallbackLLM(
            providers=[primary, secondary],
            per_attempt_timeout_s=10.0,
            total_timeout_s=30.0,
            max_attempts_per_provider=2,
            backoff_base_s=0.0,
            logger=logger,
        )

        answer = fallback.ask(prompt)

        # A resposta retornada é a do primário: provider == nome do primário.
        assert answer.provider == primary_name
        assert answer.text == "ok"
        assert answer.prompt_tokens == prompt_tokens
        assert answer.completion_tokens == completion_tokens

        # Exatamente uma linha de log de sucesso: sem fallback e result="ok".
        assert len(logger.calls) == 1
        record = logger.calls[0]
        assert record["result"] == "ok"
        assert record["used_fallback"] is False
        assert record["provider"] == primary_name
        # Apenas o primário foi tentado (o secundário nunca foi acionado).
        assert record["attempts"] == [primary_name]
