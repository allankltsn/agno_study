#!/usr/bin/env python3
"""Script de smoke test e roteiro de aceite — Agente 01 (Suporte SLA/Fallback).

Este script é o **roteiro de aceite executável** do agente: ele percorre, um a um, os
cenários do escopo §9 (`Critérios de aceite`) e imprime, para cada cenário, o que se deve
observar. É a contraparte prática do checklist em `scripts/CHECKLIST_ACEITE.md`.

Ele **não faz parte do gate de PR** (não é coletado pelo `pytest`): é uma ferramenta de
verificação manual/de demonstração. Por isso vive em `scripts/`, e não em `tests/`.

Cenários cobertos (mapeados ao escopo §9):

1. Pergunta conhecida em modo ``run``      → resposta coerente        (RF-01 / §9.1)
2. Pergunta conhecida em modo ``stream``   → fragmentos na ordem      (RF-02 / §9.2)
3. Pergunta fora de escopo                 → encaminhamento (sem alucinar) (RF-03 / §9.3)
4. Primário derrubado (chave inválida/timeout) → secundário responde e o
   **log estruturado registra o fallback**  (RF-05, RNF-02 / §9.5)
5. Inspeção do log estruturado             → provider / latência / tokens (RNF-03, RNF-05 / §9.6)

Dois modos de execução (escolhidos por ``--live`` ou pela env ``SMOKE_LIVE``):

- **Modo determinístico (padrão, sem rede):** usa dublês de ``LLMPort`` em processo
  (``_FixedProvider``, ``_OutOfScopeProvider``, ``_AlwaysFailsProvider``) montados
  diretamente no ``FallbackLLM`` com um ``StructuredLogger``. Reproduz **todos** os
  cenários de comportamento e de log **sem chaves e sem rede** — inclusive o fallback
  (cenário 4), forçando a falha do primário com um provider que sempre levanta
  ``ProviderUnavailable``. Serve para demonstrar/validar o arranjo a qualquer momento.

- **Modo ``--live`` (requer chaves/serviços reais):** usa o ``composition_root``
  (``build_answer_question``) exatamente como a CLI e a API o fazem, batendo nos providers
  reais definidos por ``MODEL_PROVIDER_ORDER`` (ex.: ``openai,groq``). Este é o caminho que
  exercita o agente ponta a ponta. Para observar o **fallback real** (cenário 4), derrube o
  primário no ambiente — por exemplo, exportando uma chave inválida para ele ou reduzindo
  ``LLM_PER_ATTEMPT_TIMEOUT`` — e observe o secundário responder no log estruturado.

Uso::

    # Determinístico (sem rede/sem chaves) — seguro em qualquer máquina:
    python scripts/smoke_test.py

    # Ao vivo (requer OPENAI_API_KEY/GROQ_API_KEY reais e MODEL_PROVIDER_ORDER):
    python scripts/smoke_test.py --live
    #   ou:  SMOKE_LIVE=1 python scripts/smoke_test.py

O script é seguro para **importar** sem chaves: nada acontece no import; o caminho ``live``
(e portanto o ``composition_root``, que lê env) só é tocado quando ``--live`` é pedido.

Compatibilidade: Python 3.9+. Sem imports de Agno (tipos da Agno ficam confinados à
``infrastructure``); os dublês determinísticos apenas implementam a ``LLMPort`` do domínio.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from contextlib import redirect_stdout
from typing import Iterator, List, Optional, Sequence

# Permite rodar o script direto (``python scripts/smoke_test.py``) a partir da raiz do
# projeto, garantindo que ``src/`` esteja no ``sys.path`` mesmo sem instalação editável.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import ProviderUnavailable
from suporte.domain.ports import Answer, LLMPort


# ---------------------------------------------------------------------------
# Pergunta conhecida (fundamentada na doc de estudo do composition_root) e uma
# pergunta claramente fora de escopo, para os cenários de aceite.
# ---------------------------------------------------------------------------
KNOWN_QUESTION = "Como redefino minha senha?"
OUT_OF_SCOPE_QUESTION = "Qual será a cotação do dólar amanhã?"


# ===========================================================================
# Dublês de LLMPort para o modo determinístico (sem rede, sem chaves).
# São apenas implementações da porta de domínio — nenhum tipo da Agno aqui.
# ===========================================================================
class _FixedProvider:
    """Provider fake que responde sempre com um ``Answer`` fixo e dentro de escopo.

    Expõe um atributo público ``name`` porque o ``FallbackLLM`` o lê (via
    ``_provider_name``) para rotular as tentativas em ``attempts`` e no log — assim as
    linhas de log usam ``fake-primary``/``fake-secondary`` em vez do rótulo posicional.
    """

    def __init__(self, name: str = "fake-primary", text: str = "") -> None:
        self.name = name
        self._name = name
        self._text = text or (
            "Acesse Configurações > Segurança > Redefinir senha e siga as "
            "instruções enviadas para o seu e-mail cadastrado."
        )

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text=self._text,
            provider=self._name,
            prompt_tokens=12,
            completion_tokens=28,
            latency_ms=42.0,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        # Emite o texto em fragmentos, na ordem — para demonstrar o modo stream.
        for word in self._text.split(" "):
            yield word + " "


class _OutOfScopeProvider:
    """Provider fake que sinaliza ``out_of_scope=True`` (pergunta fora da doc)."""

    def __init__(self, name: str = "fake-primary") -> None:
        self.name = name
        self._name = name

    def ask(self, prompt: str) -> Answer:
        return Answer(
            text="(texto do modelo suprimido pelo caso de uso)",
            provider=self._name,
            prompt_tokens=10,
            completion_tokens=0,
            latency_ms=15.0,
            out_of_scope=True,
        )

    def ask_stream(self, prompt: str) -> Iterator[str]:
        yield ""


class _AlwaysFailsProvider:
    """Provider fake que **sempre falha** — simula chave inválida/timeout do primário."""

    def __init__(self, name: str = "fake-primary") -> None:
        self.name = name
        self._name = name

    def ask(self, prompt: str) -> Answer:
        # Falha determinística: o FallbackLLM conta como tentativa esgotada e passa
        # ao próximo provider (fallback), registrando o evento no log estruturado.
        raise ProviderUnavailable(attempts=[self._name], message="chave inválida/timeout")

    def ask_stream(self, prompt: str) -> Iterator[str]:
        raise ProviderUnavailable(attempts=[self._name], message="chave inválida/timeout")


# ===========================================================================
# Helpers de apresentação
# ===========================================================================
def _titulo(numero: str, texto: str) -> None:
    print()
    print("=" * 78)
    print("CENÁRIO {} — {}".format(numero, texto))
    print("=" * 78)


def _mostrar_resposta(response: AnswerResponse) -> None:
    print("  texto        :", response.text)
    print("  provider     :", response.provider)
    print("  total_tokens :", response.total_tokens)
    print("  latency_ms   :", "{:.1f}".format(response.latency_ms))
    print("  used_fallback:", response.used_fallback)
    print("  out_of_scope :", response.out_of_scope)
    print("  escalated    :", response.escalated)


def _capturar_log(func) -> str:
    """Executa ``func`` capturando a linha JSON emitida pelo ``StructuredLogger``.

    O ``StructuredLogger`` padrão escreve a linha JSON em ``stdout`` (um ``StreamHandler``
    ligado a ``sys.stdout``). Capturamos essa saída para inspecionar o log estruturado do
    cenário sem depender de ferramentas externas.
    """
    buffer = io.StringIO()
    # O handler padrão é criado na primeira emissão apontando para o sys.stdout de então;
    # redirecionamos stdout ANTES de qualquer emissão para capturá-la de forma confiável.
    handler = logging.StreamHandler(stream=buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("suporte.request")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    with redirect_stdout(buffer):
        func()
    return buffer.getvalue()


# ===========================================================================
# Modo determinístico (sem rede) — monta o FallbackLLM à mão com os dublês.
# ===========================================================================
def _build_use_case_com_providers(providers: Sequence[LLMPort]):
    """Monta ``AnswerQuestion`` sobre um ``FallbackLLM`` com providers dados.

    Reusa o mesmo ``FallbackLLM`` e ``StructuredLogger`` da infraestrutura — só troca a
    fonte dos providers por dublês em processo (sem tocar o ``composition_root``, que leria
    env/chaves). Assim demonstramos o arranjo real de resiliência sem rede.
    """
    from suporte.application.use_cases.answer_question import AnswerQuestion
    from suporte.infrastructure.llm.fallback_llm import FallbackLLM
    from suporte.infrastructure.observability.logging import StructuredLogger

    fallback = FallbackLLM(
        list(providers),
        per_attempt_timeout_s=5.0,
        total_timeout_s=15.0,
        max_attempts_per_provider=2,
        backoff_base_s=0.01,  # backoff curtíssimo: é só demonstração, não teste de tempo
        logger=StructuredLogger(),
    )
    return AnswerQuestion(llm=fallback)


def rodar_deterministico() -> None:
    """Percorre os cinco cenários de aceite (§9) sem rede e sem chaves."""
    print("\n>>> MODO DETERMINÍSTICO (sem rede / sem chaves) <<<")
    print("Reproduz os cenários do escopo §9 com dublês de LLMPort em processo.")

    # --- Cenário 1: pergunta conhecida em modo run (§9.1) -------------------
    _titulo("1", "Pergunta conhecida, modo run (RF-01 / §9.1)")
    uc = _build_use_case_com_providers([_FixedProvider()])
    resp = uc.execute(AnswerRequest(question=KNOWN_QUESTION, mode=Mode.RUN))
    _mostrar_resposta(resp)
    print("  [esperado] resposta coerente, out_of_scope=False, escalated=False")

    # --- Cenário 2: pergunta conhecida em modo stream (§9.2) ----------------
    _titulo("2", "Pergunta conhecida, modo stream (RF-02 / §9.2)")
    uc = _build_use_case_com_providers([_FixedProvider()])
    print("  fragmentos (na ordem de emissão):")
    print("  '", end="")
    for chunk in uc.execute_stream(KNOWN_QUESTION):
        print(chunk, end="", flush=True)
    print("'")
    print("  [esperado] concatenação dos fragmentos == texto do modo run")

    # --- Cenário 3: pergunta fora de escopo (§9.3) --------------------------
    _titulo("3", "Pergunta fora de escopo — encaminhar, não alucinar (RF-03 / §9.3)")
    uc = _build_use_case_com_providers([_OutOfScopeProvider()])
    resp = uc.execute(AnswerRequest(question=OUT_OF_SCOPE_QUESTION, mode=Mode.RUN))
    _mostrar_resposta(resp)
    print("  [esperado] mensagem de encaminhamento, out_of_scope=True, escalated=True")
    print("  [esperado] o texto potencialmente alucinado do modelo NÃO aparece")

    # --- Cenário 4: derrubar o primário → fallback + log (§9.5) -------------
    _titulo("4", "Primário falha (chave inválida/timeout) → fallback (RF-05, RNF-02 / §9.5)")
    print("  Primário = _AlwaysFailsProvider; secundário = _FixedProvider.")

    resultado = {}

    def _exec_fallback() -> None:
        uc_fb = _build_use_case_com_providers(
            [_AlwaysFailsProvider(name="fake-primary"), _FixedProvider(name="fake-secondary")]
        )
        resultado["resp"] = uc_fb.execute(
            AnswerRequest(question=KNOWN_QUESTION, mode=Mode.RUN)
        )

    log_output = _capturar_log(_exec_fallback)
    _mostrar_resposta(resultado["resp"])
    print("  provider que respondeu:", resultado["resp"].provider, "(secundário)")
    print("  --- log estruturado capturado (fallback) ---")
    for linha in log_output.strip().splitlines():
        print("   ", linha)
    print("  [esperado] used_fallback=true e attempts=['fake-primary','fake-secondary']")

    # --- Cenário 5: inspecionar o log estruturado no caminho feliz (§9.6) ---
    _titulo("5", "Log estruturado: provider / latência / tokens (RNF-03, RNF-05 / §9.6)")

    def _exec_ok() -> None:
        uc_ok = _build_use_case_com_providers([_FixedProvider(name="fake-primary")])
        uc_ok.execute(AnswerRequest(question=KNOWN_QUESTION, mode=Mode.RUN))

    log_output = _capturar_log(_exec_ok)
    print("  --- log estruturado capturado (sucesso) ---")
    for linha in log_output.strip().splitlines():
        print("   ", linha)
    print("  [esperado] campos provider, latency_ms, prompt/completion/total_tokens,")
    print("             used_fallback=false, result='ok'; a pergunta aparece só como hash")

    print("\n>>> Fim do roteiro determinístico. Consulte scripts/CHECKLIST_ACEITE.md <<<")


# ===========================================================================
# Modo ao vivo — usa o composition_root (providers reais). Requer chaves.
# ===========================================================================
def rodar_live() -> None:
    """Percorre os cenários usando o ``composition_root`` (providers reais).

    Requer ``MODEL_PROVIDER_ORDER`` e as chaves dos providers escolhidos definidas no
    ambiente. Este é o mesmo caminho de montagem que a CLI e a API usam. Para observar o
    fallback (cenário 4) **ao vivo**, derrube o primário no ambiente (chave inválida ou
    ``LLM_PER_ATTEMPT_TIMEOUT`` baixo) antes de rodar.
    """
    print("\n>>> MODO AO VIVO (composition_root, providers reais) <<<")
    print("MODEL_PROVIDER_ORDER =", os.environ.get("MODEL_PROVIDER_ORDER", "(não definido)"))

    # Import tardio: só toca o composition_root (que lê env/chaves) quando --live é pedido.
    from suporte.composition_root import build_answer_question

    use_case = build_answer_question()

    _titulo("1", "Pergunta conhecida, modo run (RF-01 / §9.1)")
    resp = use_case.execute(AnswerRequest(question=KNOWN_QUESTION, mode=Mode.RUN))
    _mostrar_resposta(resp)

    _titulo("2", "Pergunta conhecida, modo stream (RF-02 / §9.2)")
    print("  '", end="")
    for chunk in use_case.execute_stream(KNOWN_QUESTION):
        print(chunk, end="", flush=True)
    print("'")

    _titulo("3", "Pergunta fora de escopo — encaminhar, não alucinar (RF-03 / §9.3)")
    resp = use_case.execute(AnswerRequest(question=OUT_OF_SCOPE_QUESTION, mode=Mode.RUN))
    _mostrar_resposta(resp)

    _titulo("4 e 5", "Fallback + log estruturado (RF-05, RNF-02, RNF-03, RNF-05 / §9.5-9.6)")
    print("  Observe a linha JSON emitida em stdout pelo StructuredLogger a cada chamada:")
    print("  - se o primário responde:  used_fallback=false, provider=<primário>")
    print("  - se você derrubou o primário: used_fallback=true, provider=<secundário>,")
    print("    attempts=[<primário>, <secundário>]")
    print("  Para forçar o fallback ao vivo, exporte uma chave inválida para o primário")
    print("  ou reduza LLM_PER_ATTEMPT_TIMEOUT e rode novamente.")


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smoke_test",
        description=(
            "Roteiro de aceite executável do Agente 01 (cenários do escopo §9). "
            "Por padrão roda em modo determinístico (sem rede/sem chaves)."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=bool(os.environ.get("SMOKE_LIVE")),
        help=(
            "Executa contra providers reais via composition_root "
            "(requer MODEL_PROVIDER_ORDER e chaves). Também ativável por SMOKE_LIVE=1."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Ponto de entrada do script. Retorna 0 em sucesso.

    Em modo determinístico, nunca precisa de rede/chaves. Em modo ``--live``, se a
    montagem falhar por configuração ausente, imprime uma mensagem amigável e retorna 1,
    sem stacktrace — deixando claro que o caminho ao vivo depende de env/chaves reais.
    """
    args = _parse_args(argv)
    if args.live:
        try:
            rodar_live()
        except Exception as exc:  # pragma: no cover - caminho ao vivo, depende de env
            # Não é parte do gate: falha de configuração no modo live vira mensagem clara.
            print(
                "\n[modo --live] Não foi possível executar contra providers reais: "
                "{}: {}".format(type(exc).__name__, exc),
                file=sys.stderr,
            )
            print(
                "Verifique MODEL_PROVIDER_ORDER e as chaves de API no ambiente.",
                file=sys.stderr,
            )
            return 1
    else:
        rodar_deterministico()
    return 0


if __name__ == "__main__":
    sys.exit(main())
