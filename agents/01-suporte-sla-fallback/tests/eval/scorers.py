"""Scorers da avaliação de eficácia (camada 2, golden set) — Req 17.5, 17.7.

Um *scorer* atribui um veredito a uma resposta do agente sobre um caso do golden set
(`dataset.jsonl`). Aqui vivem dois tipos, com naturezas bem diferentes:

- ``score_deterministic``: **grátis e sempre roda**. É Python puro, sem rede: para casos
  ``out_of_scope`` confere ``escalated == True``; para casos ``in_scope`` confere que todas
  as âncoras ``must_mention`` aparecem no texto da resposta. É a regra objetiva que sustenta
  o gate de eficácia agregado por taxa (Req 17.5).
- ``score_grounded_llm_judge``: **opcional e caro** (LLM-as-judge). Um segundo modelo julga
  a *fundamentação* da resposta. Por depender de um modelo — logo, não determinístico e
  sujeito a ruído — seu resultado é um **sinal agregado por taxa**, nunca um gate rígido por
  caso individual (Req 17.7). Esta função é deliberadamente fina e sem dependências: recebe
  o *callable* do juiz por injeção, para que a camada de eval decida quando (e se) pagá-lo.

Nota de projeto: os scorers **não** agregam nem comparam a thresholds. Quem carrega o
dataset, executa o agente, agrega por taxa e aplica os thresholds é o harness de eval
(``tests/eval/test_agent_quality.py``, tarefa 23.3). Manter os scorers puros os torna
triviais de testar e reutilizar.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence


# --------------------------------------------------------------------------- #
# Protocolo mínimo da resposta pontuada
# --------------------------------------------------------------------------- #
class _ScorableResponse(Protocol):
    """Contrato mínimo que uma resposta precisa satisfazer para ser pontuada.

    Declarado como ``Protocol`` (duck typing tipado) para que o scorer não dependa do DTO
    concreto ``AnswerResponse`` da camada de aplicação — basta expor ``text`` e ``escalated``.
    Isso mantém os scorers reutilizáveis (um fake de teste também serve) e evita acoplar a
    suíte de eval a tipos de infraestrutura.

    Attributes:
        text: Texto exibido ao usuário (onde procuramos as âncoras ``must_mention``).
        escalated: ``True`` quando a resposta foi encaminhada a um humano / degradação graciosa.
    """

    text: str
    escalated: bool


# --------------------------------------------------------------------------- #
# Normalização de texto (case- e acento-insensível)
# --------------------------------------------------------------------------- #
def _normalize(value: str) -> str:
    """Normaliza texto para comparação tolerante a caixa e acentuação.

    Aplica *casefold* (dobra de caixa mais agressiva que ``lower`` para não-ASCII), remove
    os diacríticos via decomposição Unicode (NFKD) descartando os *combining marks*, e
    colapsa espaços em branco. Assim, âncoras como ``"código"`` casam com ``"CODIGO"`` no
    texto da resposta — o que evita reprovar por diferenças cosméticas que não afetam a
    fundamentação (coerente com o espírito do Req 17.14, thresholds/eval com folga).

    Args:
        value: Texto de entrada.

    Returns:
        Texto normalizado: sem acentos, em caixa dobrada e com espaços colapsados.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.casefold().split())


# --------------------------------------------------------------------------- #
# Resultado do scorer determinístico
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeterministicScore:
    """Veredito do scorer determinístico para um único caso.

    Além do booleano ``passed`` (o que o harness agrega por taxa), carrega um ``reason``
    legível e a lista de âncoras faltantes, úteis para depurar por que um caso falhou sem
    reexecutar o agente.

    Attributes:
        passed: ``True`` se o caso satisfez a regra determinística.
        reason: Explicação curta do veredito (para logs/relatório de eval).
        missing_anchors: Âncoras ``must_mention`` ausentes no texto (vazio fora de ``in_scope``
            ou quando todas apareceram).
    """

    passed: bool
    reason: str
    missing_anchors: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        """Permite usar o resultado diretamente em contexto booleano (``if score:``)."""
        return self.passed


# --------------------------------------------------------------------------- #
# Leitura tolerante de campos do caso
# --------------------------------------------------------------------------- #
def _expected_kind(case: Mapping[str, Any]) -> str:
    """Extrai a expectativa do caso (``in_scope`` / ``out_of_scope``), tolerando o nome do campo.

    O golden set pode nomear o campo como ``expected`` ou ``expects``; aceitamos ambos para
    não acoplar o scorer a uma escolha do dataset. O valor é normalizado para minúsculas.

    Args:
        case: Dicionário do caso do dataset.

    Returns:
        A expectativa em minúsculas (ex.: ``"in_scope"``), ou ``""`` se ausente.

    Raises:
        KeyError: Nunca; ausência resulta em string vazia (tratada como caso inválido acima).
    """
    raw = case.get("expected", case.get("expects", ""))
    return str(raw).strip().lower()


def _must_mention(case: Mapping[str, Any]) -> Sequence[str]:
    """Extrai as âncoras ``must_mention`` do caso, retornando lista vazia se ausentes."""
    anchors = case.get("must_mention") or []
    return [str(a) for a in anchors]


# --------------------------------------------------------------------------- #
# Scorer determinístico (grátis, sempre roda) — Req 17.5
# --------------------------------------------------------------------------- #
def score_deterministic(
    case: Mapping[str, Any],
    response: _ScorableResponse,
) -> DeterministicScore:
    """Pontua um caso do golden set pela regra determinística (Req 17.5).

    Regra, por tipo de caso:

    - ``out_of_scope``: passa **se e somente se** ``response.escalated`` é ``True``. Alucinar
      onde deveria escalar é o pior erro do agente (Req 3, Req 17.6), por isso a checagem é
      objetiva e binária.
    - ``in_scope``: passa **se e somente se** todas as âncoras ``must_mention`` aparecem em
      ``response.text``, na comparação normalizada (sem acento, *case-insensitive*). Um caso
      ``in_scope`` sem âncoras é considerado aprovado (nada a exigir).

    É Python puro e determinístico: **não** acessa rede, arquivos nem relógio. O harness de
    eval (tarefa 23.3) chama esta função por caso e agrega ``passed`` como **taxa**,
    comparando a um threshold — nunca usando o veredito por caso como gate isolado.

    Args:
        case: Caso do dataset, com ``expected``/``expects`` e, para ``in_scope``,
            ``must_mention``.
        response: Resposta do agente, expondo ``text`` e ``escalated``.

    Returns:
        ``DeterministicScore`` com o veredito, uma justificativa e as âncoras faltantes.
    """
    kind = _expected_kind(case)

    if kind == "out_of_scope":
        if response.escalated:
            return DeterministicScore(True, "out_of_scope escalou como esperado")
        return DeterministicScore(False, "out_of_scope não escalou (escalated=False)")

    if kind == "in_scope":
        anchors = _must_mention(case)
        if not anchors:
            return DeterministicScore(True, "in_scope sem âncoras a verificar")
        normalized_text = _normalize(response.text)
        missing = tuple(a for a in anchors if _normalize(a) not in normalized_text)
        if not missing:
            return DeterministicScore(True, "in_scope mencionou todas as âncoras")
        return DeterministicScore(
            False,
            f"in_scope faltou âncora(s): {', '.join(missing)}",
            missing_anchors=missing,
        )

    # Caso mal-formado: sem expectativa reconhecível. Reprova com motivo explícito para
    # que o harness/dataset seja corrigido, em vez de mascarar um dado inválido.
    return DeterministicScore(
        False,
        f"expectativa desconhecida: {kind!r} (use 'in_scope' ou 'out_of_scope')",
    )


# --------------------------------------------------------------------------- #
# Scorer LLM-as-judge (opcional, caro) — Req 17.7
# --------------------------------------------------------------------------- #
class GroundingJudge(Protocol):
    """Contrato do juiz de fundamentação (LLM-as-judge), injetado por quem paga o custo.

    Recebe a pergunta e a resposta do agente e devolve um veredito booleano — se a resposta
    está *fundamentada* (não alucina, apoia-se na documentação). É um ``Protocol`` para que
    o harness possa plugar um agente Agno real, um cliente HTTP, ou um fake determinístico
    nos testes, sem que este módulo dependa de nenhum deles.
    """

    def __call__(self, *, question: str, answer: str) -> bool:
        """Julga se ``answer`` está fundamentado para ``question`` (``True``/``False``)."""
        ...


def score_grounded_llm_judge(
    case: Mapping[str, Any],
    response: _ScorableResponse,
    judge: Callable[..., bool],
) -> bool:
    """Pontua a *fundamentação* de uma resposta usando um juiz LLM (OPCIONAL — Req 17.7).

    Esta é a peça **opcional e cara** da eval. Diferente do scorer determinístico, o
    resultado depende de um modelo e, portanto, é ruidoso: por contrato de projeto ele é um
    **sinal agregado por taxa**, e o harness **nunca** deve usá-lo como gate rígido por caso
    individual (Req 17.7). Ou seja, um único ``False`` aqui não reprova a suíte — só a taxa
    agregada, comparada a um threshold com folga, participa da avaliação.

    A função é intencionalmente fina e sem dependências: recebe o ``judge`` por injeção
    (ver ``GroundingJudge``), delega o julgamento e devolve o booleano. Casos ``out_of_scope``
    não têm fundamentação a julgar (o esperado é escalar), então retornam ``True`` sem chamar
    o juiz — evitando custo desnecessário.

    Args:
        case: Caso do dataset (usado para pergunta e tipo esperado).
        response: Resposta do agente, expondo ``text``.
        judge: Callable no formato ``GroundingJudge`` que devolve ``True`` se a resposta está
            fundamentada. É onde mora o custo/rede — por isso injetado, nunca instanciado aqui.

    Returns:
        O veredito booleano do juiz para este caso (a ser **agregado por taxa** pelo harness).
    """
    if _expected_kind(case) == "out_of_scope":
        return True

    question = str(case.get("question", ""))
    return bool(judge(question=question, answer=response.text))
