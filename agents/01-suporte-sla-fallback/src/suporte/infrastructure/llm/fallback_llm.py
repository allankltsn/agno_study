"""`FallbackLLM` — o **núcleo de resiliência** do agente (Composite + Strategy).

Este módulo concentra toda a lógica de resiliência do agente 01 em um único
ponto, atrás da abstração `LLMPort`. Ele é, ao mesmo tempo, dois padrões:

- **Composite**: `FallbackLLM` *é uma* `LLMPort` (expõe `ask`/`ask_stream`) e, por
  dentro, é composto por uma **lista ordenada** de outras `LLMPort` (os adapters
  concretos). Como o composto satisfaz o mesmo contrato dos componentes, o caso de
  uso (`AnswerQuestion`) não distingue um adapter simples de um `FallbackLLM` (DIP).
- **Strategy**: cada provider da lista é uma *estratégia* de obtenção de resposta.
  O `FallbackLLM` percorre as estratégias na ordem de preferência e escolhe a
  próxima quando a atual falha. Acrescentar um provider é só acrescentar um item à
  lista — sem tocar em domínio nem no caso de uso (OCP).

Sobre o que este componente cuida (e por quê fica na `infrastructure`):
- **Timeout por tentativa** (RNF-01 / Req 6.2): cada chamada a um provider é
  limitada por `per_attempt_timeout_s`. Passado o prazo, a tentativa é abortada e
  contada como falha.
- **SLA total** (RNF-01 / Req 6.5): a requisição inteira (incluindo fallback) tem
  um teto `total_timeout_s`. Verificamos o tempo decorrido *antes de cada nova
  tentativa* e interrompemos, levantando `ProviderUnavailable`, sem exceder o teto
  além da margem de uma verificação de tempo.
- **Retry com backoff** (RNF-02 / Req 7.2): por provider, tentamos até
  `max_attempts_per_provider` vezes, aguardando um intervalo de **backoff
  exponencial** `backoff_base_s * 2^(n-1)` entre tentativas.
- **Fallback automático** (RF-05 / Req 5.x): esgotadas as tentativas de um provider,
  passamos ao próximo da lista e marcamos `used_fallback=True` a partir do 2º.
- **Log estruturado** (RNF-03/05): uma linha por resultado relevante (sucesso ou
  esgotamento de provider), sempre pela `question_hash` (nunca a pergunta em claro).

Testabilidade sem rede e sem `sleep` real (Req 17.2/17.3):
- `clock` é injetável (default `time.monotonic`) para que os testes controlem o
  tempo com um `FakeClock` e exercitem timeout/SLA de forma determinística.
- `sleep` **também** é injetável (default `time.sleep`). O backoff usa esse
  callable, de modo que os testes possam substituí-lo por um duplo que apenas
  avança o `FakeClock` — sem espera real e sem tornar a suíte lenta.

Sem framework: este módulo é Python puro (usa apenas `concurrent.futures` e
`time`). Nenhum import de Agno/OpenAI/Groq — a Agno fica confinada aos adapters.

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para
Python 3.9+.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable, Iterator, List, Sequence

from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer, LLMPort
from suporte.infrastructure.observability.logging import hash_question
from suporte.infrastructure.observability.tracing import (
    new_request_id,
    set_request_id_on_span,
)

__all__ = ["FallbackLLM"]


def _provider_name(provider: LLMPort, index: int) -> str:
    """Resolve um nome legível para o provider, usado em log e em `attempts`.

    O `LLMPort` (Protocol) não exige um atributo `name`; os adapters concretos
    expõem o nome via `Answer.provider`. Para nomear a tentativa **antes** de haver
    uma `Answer` (ex.: ao logar o esgotamento de um provider ou compor a lista de
    `attempts` de `ProviderUnavailable`), aceitamos qualquer um destes, na ordem:

    1. um atributo `name` no próprio provider (fakes de teste costumam expô-lo);
    2. uma constante de módulo `PROVIDER_NAME` na classe do provider (adapters);
    3. um rótulo posicional ``"provider{index}"`` como último recurso.

    Args:
        provider: A `LLMPort` cuja identificação queremos.
        index: A posição do provider na lista (0-based), usada no rótulo de fallback.

    Returns:
        Um nome legível e estável para observabilidade.
    """
    name = getattr(provider, "name", None)
    if isinstance(name, str) and name:
        return name
    provider_name = getattr(provider, "PROVIDER_NAME", None)
    if isinstance(provider_name, str) and provider_name:
        return provider_name
    return "provider{}".format(index)


class FallbackLLM:
    """Composite/Strategy que aplica timeout, retry, fallback e log sobre `LLMPort`s.

    Implementa `LLMPort` de forma **estrutural** (Protocol): expõe `ask` e
    `ask_stream` com as assinaturas esperadas, sem herança explícita. Por dentro,
    delega a uma sequência ordenada de outras `LLMPort` — o primeiro item é o
    provider **primário**, os demais são tentados por **fallback** na ordem.

    Args:
        providers: Sequência ordenada de `LLMPort` (``[primary, secondary, ...]``).
            A ordem expressa a preferência de fallback. Deve conter ao menos uma.
        per_attempt_timeout_s: Teto de tempo para **uma** chamada a um provider
            (Req 6.2). A tentativa é abortada e contada como falha ao expirar.
        total_timeout_s: Teto de tempo para a **requisição inteira**, incluindo
            fallback (Req 6.5). Verificado antes de cada nova tentativa.
        max_attempts_per_provider: Número máximo de tentativas por provider antes de
            passar ao próximo (Req 7.1). Deve ser ``>= 1``.
        backoff_base_s: Base do backoff exponencial entre tentativas de um mesmo
            provider (Req 7.2): o intervalo é ``backoff_base_s * 2 ** (n - 1)``.
        logger: Logger estruturado com o método
            ``log_request(*, question_hash, provider, latency_ms, prompt_tokens,
            completion_tokens, used_fallback, attempts, result)`` (ver
            `observability.logging.StructuredLogger`). Injetado (DIP) para desacoplar
            do destino do log e permitir um duplo em memória nos testes.
        clock: Fonte de tempo monotônica injetável (default `time.monotonic`).
            Injetável para os testes controlarem o tempo (Req 17.3), sem `sleep` real.
        sleep: Callable de espera do backoff (default `time.sleep`). Injetável para
            os testes evitarem espera real — tipicamente um duplo que apenas avança o
            `FakeClock` (Req 17.3).

    Raises:
        ValueError: Se `providers` for vazio ou `max_attempts_per_provider < 1`.
    """

    def __init__(
        self,
        providers: Sequence[LLMPort],
        per_attempt_timeout_s: float,
        total_timeout_s: float,
        max_attempts_per_provider: int,
        backoff_base_s: float,
        logger,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Pré-condições do algoritmo (design.md): ao menos um provider e ao menos
        # uma tentativa por provider. Falhamos cedo com um erro claro.
        if len(providers) < 1:
            raise ValueError("FallbackLLM exige ao menos um provider")
        if max_attempts_per_provider < 1:
            raise ValueError("max_attempts_per_provider deve ser >= 1")

        self._providers: List[LLMPort] = list(providers)
        self._per_attempt_timeout_s = per_attempt_timeout_s
        self._total_timeout_s = total_timeout_s
        self._max_attempts_per_provider = max_attempts_per_provider
        self._backoff_base_s = backoff_base_s
        self._logger = logger
        self._clock = clock
        self._sleep = sleep

    # ------------------------------------------------------------------ ask (run) ---
    def ask(self, prompt: str) -> Answer:
        """Modo *run*: retorna a `Answer` do primeiro provider que responder no SLA.

        Percorre os providers na ordem (Strategy). Para cada provider, tenta até
        `max_attempts_per_provider` vezes com backoff exponencial entre tentativas,
        respeitando o timeout por tentativa e o SLA total. Ao ter sucesso, retorna a
        `Answer` (com `provider`/`latency_ms` preenchidos) e loga ``result="ok"``. Ao
        esgotar um provider, loga um evento de fallback. Ao esgotar todos (ou atingir
        o SLA total), levanta `ProviderUnavailable(attempts)` e loga ``result="error"``.

        Args:
            prompt: A pergunta/prompt já montado a ser enviado ao provider.

        Returns:
            A `Answer` bem-sucedida, com `provider` igual ao provider que respondeu e
            `latency_ms` medido nesta chamada.

        Raises:
            ProviderUnavailable: Quando todos os providers falham/expiram ou o SLA
                total é atingido. `attempts` lista os providers na ordem tentada.
        """
        # `question_hash` é calculado uma vez: a pergunta em claro NUNCA entra no log.
        question_hash = hash_question(prompt)
        # `request_id` é gerado UMA vez no início da requisição e anexado ao span
        # OTel atual (atributo `app.request_id`). O MESMO valor é propagado a todos
        # os `log_request` abaixo, correlacionando log e trace (Req 16.4). É um id
        # opaco, sem PII — a pergunta segue só como `question_hash` (Req 16.5).
        request_id = new_request_id()
        set_request_id_on_span(request_id)
        start_time = self._clock()

        # `tried` acumula os nomes dos providers já iniciados, na ordem — é o que vai
        # em `attempts` (log e `ProviderUnavailable`). `used_fallback` vira True a
        # partir do 2º provider, ou seja, quando já tentamos pelo menos um antes.
        tried: List[str] = []
        used_fallback = False

        # --- Strategy: percorre os providers na ordem de preferência --------------
        for index, provider in enumerate(self._providers):
            name = _provider_name(provider, index)
            # A partir do 2º provider (já há um tentado), estamos em modo fallback.
            if tried:
                used_fallback = True
            tried.append(name)

            attempt = 0
            while attempt < self._max_attempts_per_provider:
                # --- SLA total: checado ANTES de iniciar cada tentativa (Req 6.5). --
                # Se já atingimos o teto total, não iniciamos nova tentativa: assim
                # não excedemos `total_timeout_s` além da margem desta verificação.
                # Logamos UMA vez o resultado de erro total e interrompemos a
                # requisição inteira (não seguimos para outros providers).
                remaining_total = self._total_timeout_s - (self._clock() - start_time)
                if remaining_total <= 0:
                    self._log_all_failed(question_hash, name, tried, request_id)
                    raise ProviderUnavailable(attempts=tried)

                # O prazo desta tentativa é o menor entre o teto por tentativa e o que
                # resta do orçamento total — nunca "estouramos" o SLA total esperando
                # por uma única tentativa lenta.
                deadline = min(self._per_attempt_timeout_s, remaining_total)

                attempt_start = self._clock()
                try:
                    answer = self._call_with_timeout(provider.ask, prompt, deadline)
                except (FuturesTimeoutError, TimeoutError, ProviderUnavailable):
                    # Falha da tentativa (timeout OU provider indisponível): conta como
                    # tentativa consumida. Só aplicamos backoff se ainda restar
                    # tentativa para ESTE provider.
                    attempt += 1
                    if attempt < self._max_attempts_per_provider:
                        self._backoff(attempt, start_time)
                    continue

                # --- Sucesso: log "ok" e retorno da Answer (com provider/latency) ---
                latency_ms = (self._clock() - attempt_start) * 1000.0
                # Preferimos o nome que o provider declara na própria Answer
                # (autoritativo); caímos no nome resolvido se vier vazio.
                winning_provider = answer.provider or name
                self._logger.log_request(
                    question_hash=question_hash,
                    provider=winning_provider,
                    latency_ms=latency_ms,
                    prompt_tokens=answer.prompt_tokens,
                    completion_tokens=answer.completion_tokens,
                    used_fallback=used_fallback,
                    attempts=list(tried),
                    result="ok",
                    request_id=request_id,
                )
                # Devolvemos uma Answer com o provider vencedor e a latência medida
                # AQUI (a do FallbackLLM), preservando os demais campos do adapter.
                return Answer(
                    text=answer.text,
                    provider=winning_provider,
                    prompt_tokens=answer.prompt_tokens,
                    completion_tokens=answer.completion_tokens,
                    latency_ms=latency_ms,
                    out_of_scope=answer.out_of_scope,
                )

            # Esgotou as tentativas deste provider → registra evento de fallback
            # (RF-05: a lista ordenada dos tentados até aqui) e segue para o próximo.
            # Para o ÚLTIMO provider, este mesmo log com `result="error"` e a lista
            # completa de `tried` é também o log de erro total da requisição (Req 5.6),
            # evitando um segundo registro redundante.
            self._log_provider_exhausted(question_hash, name, tried, request_id)

        # --- Todos os providers falharam: degradação graciosa fica a cargo do -----
        # caso de uso (AnswerQuestion); aqui levantamos o contrato de erro comum. O
        # log de `result="error"` já foi emitido acima, ao esgotar o último provider.
        raise ProviderUnavailable(attempts=tried)

    # -------------------------------------------------------------- ask_stream -----
    def ask_stream(self, prompt: str) -> Iterator[str]:
        """Modo *stream*: gera fragmentos do primeiro provider que abrir o stream no SLA.

        Análogo a `ask`, respeitando a ordem de fallback e o timeout por tentativa,
        porém adaptado ao streaming: o timeout protege a **abertura** do stream
        (materializamos os fragmentos dentro do prazo da tentativa) e, aberto com
        sucesso, os fragmentos são repassados na ordem de emissão. Ao esgotar um
        provider, loga o evento de fallback; ao esgotar todos (ou atingir o SLA
        total), levanta `ProviderUnavailable(attempts)`.

        Nota de implementação: para respeitar o deadline com o mesmo mecanismo do
        modo run, a coleta dos fragmentos do provider ocorre dentro de
        `call_with_timeout` (a função worker consome o iterador e devolve a lista de
        chunks). Isso mantém o comportamento de timeout uniforme entre os modos; para
        respostas muito grandes, é um trade-off consciente (materializar antes de
        emitir) em nome de um SLA por tentativa simples e determinístico.

        Args:
            prompt: A pergunta/prompt já montado a ser enviado ao provider.

        Yields:
            Pedaços de texto (`str`) na ordem de emissão do provider vencedor.

        Raises:
            ProviderUnavailable: Quando todos os providers falham/expiram ou o SLA
                total é atingido. `attempts` lista os providers na ordem tentada.
        """
        question_hash = hash_question(prompt)
        # Mesmo contrato do modo run: um `request_id` por requisição, anexado ao
        # span (`app.request_id`) e propagado a todos os logs (Req 16.4/16.5).
        request_id = new_request_id()
        set_request_id_on_span(request_id)
        start_time = self._clock()

        tried: List[str] = []
        used_fallback = False

        for index, provider in enumerate(self._providers):
            name = _provider_name(provider, index)
            if tried:
                used_fallback = True
            tried.append(name)

            attempt = 0
            while attempt < self._max_attempts_per_provider:
                # SLA total verificado antes de cada tentativa (mesma regra do run).
                remaining_total = self._total_timeout_s - (self._clock() - start_time)
                if remaining_total <= 0:
                    self._log_all_failed(question_hash, name, tried, request_id)
                    raise ProviderUnavailable(attempts=tried)

                deadline = min(self._per_attempt_timeout_s, remaining_total)

                attempt_start = self._clock()
                try:
                    # Materializa os fragmentos dentro do prazo da tentativa: o worker
                    # consome o iterador do provider e devolve a lista de chunks.
                    chunks = self._call_with_timeout(
                        lambda p: list(provider.ask_stream(p)), prompt, deadline
                    )
                except (FuturesTimeoutError, TimeoutError, ProviderUnavailable):
                    attempt += 1
                    if attempt < self._max_attempts_per_provider:
                        self._backoff(attempt, start_time)
                    continue

                # Sucesso: loga "ok" e emite os fragmentos na ordem coletada.
                latency_ms = (self._clock() - attempt_start) * 1000.0
                self._logger.log_request(
                    question_hash=question_hash,
                    provider=name,
                    latency_ms=latency_ms,
                    prompt_tokens=0,
                    completion_tokens=0,
                    used_fallback=used_fallback,
                    attempts=list(tried),
                    result="ok",
                    request_id=request_id,
                )
                for chunk in chunks:
                    yield chunk
                return

            # Provider esgotado → evento de fallback e próximo provider. Para o
            # último, este log com `result="error"` e a lista completa cobre o erro
            # total da requisição (Req 5.6), sem registro redundante.
            self._log_provider_exhausted(question_hash, name, tried, request_id)

        # Todos falharam. O log de erro já foi emitido ao esgotar o último provider.
        raise ProviderUnavailable(attempts=tried)

    # ----------------------------------------------------------------- helpers -----
    def _call_with_timeout(self, fn, prompt: str, deadline_s: float):
        """Executa `fn(prompt)` com um deadline duro, levantando `TimeoutError` ao expirar.

        Mecanismo (design.md): submete `fn` a um `ThreadPoolExecutor` e aguarda o
        resultado com `future.result(timeout=deadline_s)`. Se o prazo expira, o
        `concurrent.futures.TimeoutError` é convertido em `TimeoutError` (contrato do
        algoritmo) e a `future` é cancelada — não bloqueamos além do prazo à espera do
        worker (o executor é fechado sem `wait`, abandonando a tarefa pendente).

        Observação: em CPython, uma thread bloqueada em I/O de rede não é
        "interrompível" de fora; o SDK do provider recebe seu próprio timeout de rede
        como **defesa em profundidade**. Aqui garantimos que o *chamador* não espera
        além de `deadline_s`, que é o que o SLA por tentativa exige.

        Args:
            fn: Callable de uma tentativa (ex.: ``provider.ask`` ou um lambda que
                consome ``provider.ask_stream``).
            prompt: Argumento repassado a `fn`.
            deadline_s: Prazo máximo, em segundos (> 0).

        Returns:
            O valor retornado por `fn(prompt)` quando concluído dentro do prazo.

        Raises:
            TimeoutError: Quando `fn` não conclui dentro de `deadline_s`.
            ProviderUnavailable: Propagada se `fn` a levantar (contrato de erro comum
                dos adapters — LSP).
        """
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fn, prompt)
        try:
            result = future.result(timeout=deadline_s)
        except FuturesTimeoutError as exc:
            # Prazo estourado: cancela a future (best-effort) e sinaliza timeout no
            # contrato do algoritmo. Não esperamos o worker terminar.
            future.cancel()
            raise TimeoutError("tentativa excedeu o deadline") from exc
        finally:
            # Não bloqueamos no shutdown: a tarefa pendente (se houver) é abandonada
            # para que o chamador nunca espere além do prazo da tentativa.
            executor.shutdown(wait=False)
        return result

    def _backoff(self, attempt: int, start_time: float) -> None:
        """Aguarda o backoff exponencial antes da próxima tentativa (Req 7.2).

        O intervalo cresce como ``backoff_base_s * 2 ** (attempt - 1)``, onde
        `attempt` é o número de tentativas já feitas neste provider (>= 1). A espera
        usa o `sleep` **injetado** (default `time.sleep`), o que permite aos testes
        substituí-lo por um duplo que apenas avança o `FakeClock` — sem espera real.

        SLA total (Req 6.5): o backoff **nunca** dorme além do orçamento total
        restante. O intervalo é limitado ao tempo que ainda resta de `total_timeout_s`
        no momento da espera; se o orçamento já se esgotou, não dormimos. Sem esse
        teto, um backoff longo entre tentativas de um mesmo provider poderia empurrar o
        tempo decorrido muito além do teto total — a próxima verificação de SLA só
        ocorre no topo do laço, *depois* da espera. Com o teto, o único excedente
        possível é o custo da última tentativa iniciada dentro do orçamento (a margem
        de uma verificação), como exige a Property 4.

        Args:
            attempt: Quantidade de tentativas já consumidas para o provider atual
                (usada no expoente do backoff).
            start_time: Instante (no `clock` injetado) em que a requisição começou,
                usado para calcular o orçamento total restante.
        """
        interval = self._backoff_base_s * (2 ** (attempt - 1))
        # Limita o backoff ao orçamento total restante: nunca dormimos para além de
        # `total_timeout_s`. Assim o backoff não excede o SLA total (Req 6.5).
        remaining_total = self._total_timeout_s - (self._clock() - start_time)
        interval = min(interval, remaining_total)
        if interval > 0:
            self._sleep(interval)

    def _log_provider_exhausted(
        self,
        question_hash: str,
        provider_name: str,
        tried: List[str],
        request_id: str,
    ) -> None:
        """Loga o evento de fallback quando um provider esgota suas tentativas (RF-05).

        Registra a lista ordenada dos providers tentados até aqui e
        ``used_fallback=True`` (houve — ou haverá — mudança de provider). Não é o log
        final de erro da requisição; é o registro do *evento de fallback* deste
        provider antes de tentar o próximo.

        Args:
            question_hash: Hash determinístico da pergunta (nunca a pergunta em claro).
            provider_name: Nome do provider cujas tentativas se esgotaram.
            tried: Providers tentados até o momento, na ordem.
            request_id: Identificador da requisição, gravado como campo para
                correlacionar com o atributo `app.request_id` do span (Req 16.4).
        """
        self._logger.log_request(
            question_hash=question_hash,
            provider=provider_name,
            latency_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
            used_fallback=True,
            attempts=list(tried),
            result="error",
            request_id=request_id,
        )

    def _log_all_failed(
        self,
        question_hash: str,
        provider_name: str,
        tried: List[str],
        request_id: str,
    ) -> None:
        """Loga o resultado de erro total da requisição (Req 5.6, 9.4).

        Emitido imediatamente antes de levantar `ProviderUnavailable`, com a lista
        ordenada de providers tentados, ``used_fallback=True`` e ``result="error"``.

        Args:
            question_hash: Hash determinístico da pergunta.
            provider_name: Último provider tentado (ou ``"none"`` se nenhum).
            tried: Providers tentados, na ordem.
            request_id: Identificador da requisição, gravado como campo para
                correlacionar com o atributo `app.request_id` do span (Req 16.4).
        """
        self._logger.log_request(
            question_hash=question_hash,
            provider=provider_name,
            latency_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
            used_fallback=True,
            attempts=list(tried),
            result="error",
            request_id=request_id,
        )
