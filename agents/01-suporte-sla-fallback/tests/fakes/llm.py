"""Fakes reutilizáveis da `LLMPort` e um relógio controlável (Req 17.2 / 17.3).

Estes duplos formam a base da **camada de transporte determinístico** da estratégia
de testes (Req 17.1): nenhum deles toca a rede, nenhum usa `sleep` de parede e o
tempo, quando importa, é simulado por um `FakeClock` explícito. Assim os testes de
`AnswerQuestion` e de resiliência do `FallbackLLM` (timeout por tentativa, backoff,
SLA total) rodam rápido e sem intermitência, compondo o gate obrigatório de cada
mudança.

Convenção de conformidade: a `LLMPort` é um `Protocol` (duck typing tipado). Cada
fake aqui expõe `ask(prompt) -> Answer` e `ask_stream(prompt) -> Iterator[str]`, de
modo que `isinstance(fake, LLMPort)` é verdadeiro e o `FallbackLLM`/`AnswerQuestion`
os aceita sem herança explícita.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer


# --------------------------------------------------------------------------- #
# Relógio virtual
# --------------------------------------------------------------------------- #
class FakeClock:
    """Relógio monotônico virtual e controlável, compatível com `time.monotonic`.

    Substitui `time.monotonic` no `FallbackLLM` (parâmetro `clock`). Começa em
    `start` (default `0.0`) e só avança quando `advance(seconds)` é chamado — seja
    diretamente pelo teste, por um provider que simula o tempo de uma tentativa
    (ver `SlowProvider`) ou pelo duplo de `sleep` do backoff. Chamar a instância
    (`__call__`) devolve o tempo virtual atual, exatamente como `time.monotonic()`.

    Por não haver espera de parede, os testes de timeout/backoff/SLA são
    determinísticos e instantâneos (Req 17.3).

    Args:
        start: Instante inicial do relógio virtual, em segundos.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def advance(self, seconds: float) -> None:
        """Avança o tempo virtual em `seconds` (espera-se `>= 0`)."""
        self._now += float(seconds)

    def __call__(self) -> float:
        """Devolve o tempo virtual atual, como `time.monotonic()`."""
        return self._now


# --------------------------------------------------------------------------- #
# FakeLLM: resposta fixa (ou out_of_scope), ou erro configurado
# --------------------------------------------------------------------------- #
class FakeLLM:
    """`LLMPort` em memória que devolve um `Answer` fixo — ou levanta um erro.

    É o duplo base da camada de transporte (Req 17.2) e o usado para testar
    `AnswerQuestion` sem rede. Comportamento configurável:

    - Se `raise_error` for fornecido, `ask`/`ask_stream` levantam essa exceção
      (útil para simular `ProviderUnavailable` — falha total — ou outro erro de
      domínio).
    - Caso contrário, `ask` retorna um `Answer` fixo. Ele pode ser passado pronto
      via `answer`, ou construído a partir de `text`/`provider`/`out_of_scope`
      (atalho para o caso comum de resposta dentro ou fora de escopo).
    - `ask_stream` emite `chunks` quando fornecidos; senão, emite o texto do
      `Answer` como um único fragmento.

    Registra `ask_calls`, `ask_stream_calls` e `prompts` para permitir asserir que
    o caso de uso **não** invoca a port em cenários de validação (pergunta vazia,
    modo inválido) e para inspecionar o prompt montado.

    Args:
        answer: `Answer` fixo a devolver. Se omitido e `raise_error` também, um é
            construído a partir de `text`/`provider`/`out_of_scope`.
        raise_error: Exceção a levantar em `ask`/`ask_stream` (tem precedência).
        chunks: Fragmentos a emitir em `ask_stream`; default é `[answer.text]`.
        text: Texto do `Answer` construído quando `answer` é omitido.
        provider: Nome do provider do `Answer` construído.
        out_of_scope: Marca o `Answer` construído como fora de escopo.
    """

    def __init__(
        self,
        answer: Optional[Answer] = None,
        *,
        raise_error: Optional[Exception] = None,
        chunks: Optional[List[str]] = None,
        text: str = "resposta",
        provider: str = "fake",
        out_of_scope: bool = False,
    ) -> None:
        if answer is None and raise_error is None:
            answer = Answer(
                text=text,
                provider=provider,
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=0.0,
                out_of_scope=out_of_scope,
            )
        self._answer = answer
        self._raise_error = raise_error
        self._chunks = chunks
        self.ask_calls = 0
        self.ask_stream_calls = 0
        self.prompts: List[str] = []

    def ask(self, prompt: str) -> Answer:
        self.ask_calls += 1
        self.prompts.append(prompt)
        if self._raise_error is not None:
            raise self._raise_error
        assert self._answer is not None, "FakeLLM sem answer nem raise_error configurado"
        return self._answer

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.ask_stream_calls += 1
        self.prompts.append(prompt)
        if self._raise_error is not None:
            raise self._raise_error
        if self._chunks is not None:
            yield from self._chunks
            return
        assert self._answer is not None, "FakeLLM sem answer nem raise_error configurado"
        yield self._answer.text


# --------------------------------------------------------------------------- #
# AlwaysOk / AlwaysFails: respostas triviais para o FallbackLLM
# --------------------------------------------------------------------------- #
class AlwaysOk:
    """`LLMPort` fake que responde **sempre** com sucesso e no ato (dentro do SLA).

    Útil como provider primário "saudável" em testes de fallback: como retorna na
    hora, o timeout de parede nunca dispara e o SLA total nunca é atingido.

    Args:
        name: Nome do provider (aparece em `Answer.provider`).
        text: Texto da resposta devolvida.
    """

    def __init__(self, name: str, text: str = "resposta") -> None:
        self.name = name
        self._text = text
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        return Answer(
            text=self._text,
            provider=self.name,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=0.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.calls += 1
        yield self._text


class AlwaysFails:
    """`LLMPort` fake cujo `ask`/`ask_stream` **sempre** falham com `ProviderUnavailable`.

    Cada chamada conta como uma tentativa consumida pelo `FallbackLLM` (`calls`) e
    nomeia a tentativa em `attempts`/no log via `name`. Retorna imediatamente
    (levanta na hora), de modo que o `ThreadPoolExecutor` de `_call_with_timeout`
    resolve a future na hora e o timeout de parede nunca dispara — o portão
    exercitado é o de exaustão total, não o de tempo.

    Args:
        name: Nome do provider, usado em `ProviderUnavailable(attempts=[name])`.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        raise ProviderUnavailable(attempts=[self.name])

    def ask_stream(self, prompt: str) -> Iterator[str]:
        self.calls += 1
        raise ProviderUnavailable(attempts=[self.name])
        yield ""  # pragma: no cover - torna a função um gerador


# --------------------------------------------------------------------------- #
# SlowProvider: consome tempo virtual antes de responder/falhar
# --------------------------------------------------------------------------- #
class SlowProvider:
    """`LLMPort` fake que consome tempo VIRTUAL (via `FakeClock`) por chamada.

    Cada chamada a `ask`/`ask_stream` avança o `FakeClock` injetado em `delay_s`
    segundos — simulando o tempo gasto por uma tentativa — e então responde com um
    `Answer` fixo ou, se `fail=True`, levanta `ProviderUnavailable`. É o duplo que
    exercita o SLA total e o timeout por tentativa sem espera de parede (Req 17.3):
    ao asserir que nenhuma nova tentativa começa após o orçamento total, contamos
    `calls`.

    Nota: o avanço é do tempo virtual, não uma espera real; combine com um
    `total_timeout_s`/`per_attempt_timeout_s` no `FallbackLLM` para exercitar os
    portões de tempo.

    Args:
        name: Nome do provider (aparece em `Answer.provider`/`attempts`).
        clock: `FakeClock` compartilhado que este provider avança a cada chamada.
        delay_s: Segundos de tempo virtual consumidos por chamada (`>= 0`).
        fail: Quando `True`, levanta `ProviderUnavailable` após avançar o relógio;
            quando `False`, devolve um `Answer` fixo.
        text: Texto da resposta quando `fail=False`.
    """

    def __init__(
        self,
        name: str,
        clock: FakeClock,
        delay_s: float,
        *,
        fail: bool = False,
        text: str = "resposta",
    ) -> None:
        self.name = name
        self._clock = clock
        self._delay_s = float(delay_s)
        self._fail = fail
        self._text = text
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        self._clock.advance(self._delay_s)
        if self._fail:
            raise ProviderUnavailable(attempts=[self.name])
        return Answer(
            text=self._text,
            provider=self.name,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=self._delay_s * 1000.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        answer = self.ask(prompt)
        yield answer.text


# --------------------------------------------------------------------------- #
# ProgrammableAdapter: falha N vezes (ou sempre) e então responde
# --------------------------------------------------------------------------- #
class ProgrammableAdapter:
    """`LLMPort` fake que falha um nº configurável de vezes e então responde.

    Simula um provider com falhas transitórias: as primeiras `fail_times` chamadas
    a `ask` levantam `ProviderUnavailable`; a partir daí, responde com um `Answer`
    fixo (`provider = name`). É o "provider programável" pedido pelo Req 17.2, usado
    para exercitar o backoff entre re-tentativas do mesmo provider e o fallback
    determinístico. Conta chamadas em `calls`.

    Para "falhar sempre", passe `fail_always=True` (equivale a um número infinito de
    falhas, independente de `fail_times`).

    Se um `clock` for fornecido, cada chamada avança o tempo virtual em `delay_s`
    segundos — permitindo medir só o overhead da orquestração (sem LLM real) ou
    exercitar os portões de tempo sem espera de parede (Req 17.3). Sem `clock`, o
    provider responde/ falha no ato.

    Args:
        name: Nome do provider (aparece em `Answer.provider` e em `attempts`).
        fail_times: Quantas chamadas iniciais devem falhar antes do sucesso.
        fail_always: Quando `True`, todas as chamadas falham (ignora `fail_times`).
        delay_s: Tempo virtual consumido por chamada quando `clock` é fornecido.
        clock: `FakeClock` opcional a avançar em `delay_s` a cada chamada.
        text: Texto da resposta de sucesso; default `"resposta de <name>"`.
    """

    def __init__(
        self,
        name: str,
        fail_times: int = 0,
        *,
        fail_always: bool = False,
        delay_s: float = 0.0,
        clock: Optional[FakeClock] = None,
        text: Optional[str] = None,
    ) -> None:
        self.name = name
        self._fail_times = fail_times
        self._fail_always = fail_always
        self._delay_s = float(delay_s)
        self._clock = clock
        self._text = text if text is not None else "resposta de {}".format(name)
        self.calls = 0

    def ask(self, prompt: str) -> Answer:
        self.calls += 1
        if self._clock is not None and self._delay_s:
            self._clock.advance(self._delay_s)
        if self._fail_always or self.calls <= self._fail_times:
            raise ProviderUnavailable(attempts=[self.name])
        return Answer(
            text=self._text,
            provider=self.name,
            prompt_tokens=1,
            completion_tokens=2,
            latency_ms=self._delay_s * 1000.0,
            out_of_scope=False,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        answer = self.ask(prompt)
        yield answer.text
