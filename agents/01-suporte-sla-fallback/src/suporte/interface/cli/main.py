"""Interface de linha de comando (CLI) — ponto de entrada fino do agente.

Esta é a borda de entrada mais simples do sistema. Sua única responsabilidade é
*adaptar* a linha de comando ao caso de uso `AnswerQuestion`, sem conter nenhuma
regra de negócio:

1. Ler e **validar** a entrada na borda (tamanho da pergunta 1..4000; modo dentro
   do conjunto finito de `Mode`), rejeitando entrada inválida com uma mensagem de
   validação **antes** de tocar no caso de uso (Req 11.2, 11.3).
2. Montar o caso de uso via `composition_root.build_answer_question()` — o único
   lugar que conhece as implementações concretas (Req 11.1). A CLI só enxerga
   `AnswerRequest`/`AnswerResponse` e o caso de uso; **nunca** tipos da Agno.
3. Chamar `execute` (modo `run`) ou `execute_stream` (modo `stream`) e **formatar**
   a saída, incluindo os metadados de observabilidade (`provider`, `total_tokens`,
   `latency_ms`, `used_fallback`) (Req 11.1).
4. Traduzir qualquer `DomainError` em uma mensagem amigável, **sem** exibir
   stacktrace nem detalhes internos (Req 11.6).

Por que a validação de tamanho/modo mora aqui (e não no caso de uso): é uma regra
da *borda* (interface), simétrica à validação Pydantic da API FastAPI (Req 14.2/
14.3). O caso de uso valida apenas a regra de aplicação (pergunta não vazia); a
interface é responsável por rejeitar entrada malformada sem gastar uma chamada ao
caso de uso (Req 11.2). Isso mantém o custo controlado (curto-circuito) e o
contrato da borda explícito.

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para
Python 3.9+. Sem imports de Agno (tipos da Agno ficam confinados à
`infrastructure`).
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from suporte.application.dto import AnswerRequest, AnswerResponse, Mode
from suporte.domain.exceptions import DomainError

# Limites de validação da pergunta na borda (Req 11.2). Simétricos aos limites
# Pydantic da API FastAPI (`min_length=1`, `max_length=4000`).
_MIN_QUESTION_LEN = 1
_MAX_QUESTION_LEN = 4000

# Códigos de saída do processo: 0 = sucesso; 2 = erro de validação de entrada
# (convenção do `argparse` para uso incorreto). Erros de domínio traduzidos NÃO
# são falha do processo — imprimem mensagem amigável e saem com 0.
_EXIT_OK = 0
_EXIT_VALIDATION = 2

# Mensagem genérica de degradação para erros de domínio (Req 11.6). O texto de
# `AnswerResponse` já cobre os caminhos tratados pelo caso de uso; esta mensagem
# é a rede de segurança para qualquer `DomainError` que suba até a borda, sempre
# **sem** stacktrace nem detalhes internos.
_DOMAIN_ERROR_MESSAGE = (
    "Nosso assistente está indisponível no momento. "
    "Tente novamente em instantes."
)


def _build_parser() -> argparse.ArgumentParser:
    """Monta o parser de argumentos da CLI.

    Define a pergunta como argumento posicional e o modo como opção `--mode`,
    restrita ao conjunto finito de `Mode` (`run`/`stream`). Deixar o `argparse`
    validar as *choices* do modo garante que um modo inválido seja rejeitado com
    uma mensagem indicando os modos suportados, **sem** chamar o caso de uso
    (Req 11.3).

    Returns:
        Um `argparse.ArgumentParser` configurado.
    """
    parser = argparse.ArgumentParser(
        prog="suporte",
        description=(
            "Agente 01 — Suporte com SLA e Fallback de Provider. "
            "Envie uma pergunta e receba uma resposta fundamentada na "
            "documentação do produto."
        ),
    )
    parser.add_argument(
        "question",
        help="Pergunta do cliente (1 a 4000 caracteres).",
    )
    parser.add_argument(
        "--mode",
        # `choices` com os valores do enum garante a rejeição de modo inválido
        # na borda, com mensagem listando os suportados (Req 11.3).
        choices=[m.value for m in Mode],
        default=Mode.RUN.value,
        help="Modo de resposta: 'run' (completa) ou 'stream' (incremental).",
    )
    return parser


def _validate_question(question: Optional[str]) -> Optional[str]:
    """Valida o tamanho da pergunta na borda (Req 11.2).

    Rejeita pergunta ausente, vazia/apenas espaços ou com mais de 4000 caracteres,
    retornando uma **mensagem de validação** — o chamador não deve invocar o caso
    de uso quando esta função retorna algo diferente de ``None``.

    O comprimento é medido sobre a pergunta com espaços das pontas removidos, de
    modo que uma pergunta feita só de espaços conte como vazia (comprimento 0) e
    seja rejeitada.

    Args:
        question: Texto da pergunta recebido da linha de comando (pode ser
            ``None`` quando ausente).

    Returns:
        ``None`` quando a pergunta é válida; caso contrário, a mensagem de
        validação a ser exibida ao usuário.
    """
    if question is None:
        return "Envie uma pergunta válida (1 a 4000 caracteres)."

    length = len(question.strip())
    if length < _MIN_QUESTION_LEN:
        return "Envie uma pergunta válida (1 a 4000 caracteres)."
    if length > _MAX_QUESTION_LEN:
        return (
            "Pergunta muito longa: são permitidos no máximo "
            f"{_MAX_QUESTION_LEN} caracteres."
        )
    return None


def _format_response(response: AnswerResponse) -> str:
    """Formata o `AnswerResponse` para exibição na CLI (Req 11.1).

    Além do texto da resposta, inclui os metadados de observabilidade exigidos
    (`provider`, `total_tokens`, `latency_ms`, `used_fallback`) para que o usuário
    possa conferir qual provider respondeu, o custo em tokens, a latência e se
    houve fallback.

    Args:
        response: Saída do caso de uso.

    Returns:
        Texto pronto para impressão (resposta + bloco de metadados).
    """
    lines: List[str] = [
        response.text,
        "",
        "--- metadados ---",
        f"provider: {response.provider}",
        f"total_tokens: {response.total_tokens}",
        f"latency_ms: {response.latency_ms:.1f}",
        f"used_fallback: {response.used_fallback}",
    ]
    return "\n".join(lines)


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    """Executa a CLI: valida a entrada, chama o caso de uso e formata a saída.

    Fluxo (Req 11.1–11.3, 11.6):
    1. `argparse` interpreta os argumentos; um `--mode` fora do conjunto finito é
       rejeitado por ele com mensagem listando os modos suportados (sem chamar o
       caso de uso).
    2. Valida o tamanho da pergunta (1..4000). Se inválida, imprime a mensagem de
       validação e retorna sem montar/chamar o caso de uso (Req 11.2).
    3. Só então monta o caso de uso via `composition_root` e executa no modo
       escolhido, imprimindo a saída formatada com os metadados (Req 11.1).
    4. Qualquer `DomainError` que suba até aqui vira mensagem amigável, **sem**
       stacktrace (Req 11.6).

    Args:
        argv: Lista de argumentos (para teste). Quando ``None``, usa
            ``sys.argv[1:]``.

    Returns:
        Código de saída do processo: ``0`` em sucesso ou erro de domínio tratado;
        ``2`` em erro de validação de entrada.
    """
    parser = _build_parser()
    # `parse_args` já rejeita `--mode` inválido (Req 11.3) e ausência da pergunta,
    # saindo com código 2 antes de qualquer chamada ao caso de uso.
    args = parser.parse_args(argv)

    # Validação de tamanho da pergunta na borda, ANTES de montar/chamar o caso de
    # uso (Req 11.2). Curto-circuita para controlar custo.
    validation_message = _validate_question(args.question)
    if validation_message is not None:
        print(validation_message, file=sys.stderr)
        return _EXIT_VALIDATION

    # Import tardio: só montamos o caso de uso (que lê configuração/env) depois de
    # a entrada ter passado na validação da borda.
    from suporte.composition_root import build_answer_question

    mode = Mode(args.mode)

    try:
        use_case = build_answer_question()

        if mode is Mode.STREAM:
            # Modo stream: repassa os fragmentos na ordem de emissão (RF-02).
            for chunk in use_case.execute_stream(args.question):
                print(chunk, end="", flush=True)
            print()  # quebra de linha final após o stream
        else:
            request = AnswerRequest(question=args.question, mode=mode)
            response = use_case.execute(request)
            print(_format_response(response))
    except DomainError:
        # Req 11.6: traduzir erro de domínio em mensagem amigável, sem stacktrace
        # nem detalhes internos. Não é falha do processo (saída 0).
        print(_DOMAIN_ERROR_MESSAGE, file=sys.stderr)
        return _EXIT_OK

    return _EXIT_OK


def main() -> None:
    """Ponto de entrada de console: executa a CLI e propaga o código de saída."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
