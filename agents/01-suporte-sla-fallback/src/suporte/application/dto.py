"""DTOs de entrada e saída do caso de uso.

Este módulo define os *Data Transfer Objects* que desacoplam a `interface` (CLI, API
FastAPI) da assinatura interna do caso de uso `AnswerQuestion`. A `interface` fala em
termos de `AnswerRequest`/`AnswerResponse`, e não em parâmetros soltos — o que mantém o
contrato estável mesmo que a orquestração interna evolua.

São Python puro (`dataclasses` + `enum`), sem qualquer dependência de framework (Agno,
provider SDKs). Isso preserva o isolamento da camada de aplicação (DIP): a `interface`
depende destes DTOs, e não de tipos de infraestrutura.

`AnswerResponse` carrega, além do texto, metadados de observabilidade
(`provider`, `total_tokens`, `latency_ms`, `used_fallback`, `out_of_scope`, `escalated`)
para a `interface` renderizar ao usuário e para conferência dos critérios de aceite.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """Modo de execução solicitado pela `interface`.

    Herdar de ``str`` torna o enum diretamente serializável e comparável a strings
    (``Mode.RUN == "run"``), o que facilita a validação na borda (Pydantic, CLI) e a
    escrita em logs, mantendo um conjunto **finito** e explícito de valores válidos.

    Attributes:
        RUN: Resposta completa em uma única chamada (``ask``).
        STREAM: Resposta emitida em fragmentos, na ordem de geração (``ask_stream``).
    """

    RUN = "run"
    STREAM = "stream"


@dataclass(frozen=True)
class AnswerRequest:
    """Entrada do caso de uso `AnswerQuestion`.

    Imutável (``frozen=True``) para que o pedido não seja alterado ao atravessar as
    camadas. O modo assume ``Mode.RUN`` por padrão, tornando o uso comum (resposta
    completa) o caminho mais simples.

    Attributes:
        question: Pergunta do usuário (validação de tamanho ocorre na `interface`).
        mode: Modo de execução desejado; padrão ``Mode.RUN``.
    """

    question: str
    mode: Mode = Mode.RUN


@dataclass(frozen=True)
class AnswerResponse:
    """Saída do caso de uso `AnswerQuestion`.

    Imutável (``frozen=True``). Além do texto da resposta, expõe metadados de
    observabilidade que a `interface` renderiza e que permitem conferir os critérios de
    aceite (qual provider respondeu, custo em tokens, latência, se houve fallback,
    se estava fora de escopo e se foi encaminhado a um humano).

    Attributes:
        text: Texto a ser exibido ao usuário (resposta ou mensagem de degradação).
        provider: Nome do provider que respondeu (ex.: ``"openai"``, ``"groq"``).
        total_tokens: Total de tokens consumidos (prompt + completion).
        latency_ms: Latência da resposta em milissegundos.
        used_fallback: ``True`` quando um provider secundário respondeu.
        out_of_scope: ``True`` quando a pergunta ficou fora do escopo da documentação.
        escalated: ``True`` quando encaminhado a um humano ou em degradação graciosa.
    """

    text: str
    provider: str
    total_tokens: int
    latency_ms: float
    used_fallback: bool
    out_of_scope: bool
    escalated: bool
