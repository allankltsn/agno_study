"""Testes de unidade do value object `Answer` (domain/ports.py).

Cobrem o contrato do value object imutável usado como resultado de qualquer
`LLMPort`:

- `total_tokens` é a soma de `prompt_tokens` + `completion_tokens` (Req 8.5, 1.2);
- `out_of_scope` tem default `False` (dentro de escopo);
- a `Answer` é imutável (`frozen=True`): nenhum campo pode ser reatribuído.

Além dos exemplos pontuais, um teste de propriedade (`hypothesis`) verifica que
`total_tokens == prompt_tokens + completion_tokens` para qualquer par de inteiros
não negativos — a invariante central de derivação de custo.
"""

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.domain.ports import Answer


def _make_answer(prompt_tokens: int = 3, completion_tokens: int = 5, **overrides) -> Answer:
    """Constrói uma `Answer` de teste com valores padrão sobrescrevíveis."""
    fields = dict(
        text="resposta",
        provider="openai",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=12.5,
    )
    fields.update(overrides)
    return Answer(**fields)


class TestAnswerTotalTokens:
    """`total_tokens` deve derivar exatamente a soma entrada + saída (Req 8.5)."""

    def test_total_tokens_soma_prompt_e_completion(self) -> None:
        answer = _make_answer(prompt_tokens=10, completion_tokens=7)
        assert answer.total_tokens == 17

    def test_total_tokens_com_zeros(self) -> None:
        answer = _make_answer(prompt_tokens=0, completion_tokens=0)
        assert answer.total_tokens == 0

    @given(
        prompt_tokens=st.integers(min_value=0, max_value=10_000_000),
        completion_tokens=st.integers(min_value=0, max_value=10_000_000),
    )
    def test_total_tokens_e_sempre_a_soma(
        self, prompt_tokens: int, completion_tokens: int
    ) -> None:
        """total_tokens == prompt_tokens + completion_tokens para todo par >= 0.

        **Validates: Requirements 8.5**
        """
        answer = _make_answer(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
        assert answer.total_tokens == prompt_tokens + completion_tokens


class TestAnswerOutOfScopeDefault:
    """`out_of_scope` deve assumir `False` quando não informado (Req 1.2)."""

    def test_default_out_of_scope_e_false(self) -> None:
        answer = _make_answer()
        assert answer.out_of_scope is False

    def test_out_of_scope_pode_ser_true_quando_explicito(self) -> None:
        answer = _make_answer(out_of_scope=True)
        assert answer.out_of_scope is True


class TestAnswerImutabilidade:
    """`Answer` é `frozen=True`: reatribuir campo levanta `FrozenInstanceError`."""

    def test_nao_permite_reatribuir_text(self) -> None:
        answer = _make_answer()
        with pytest.raises(FrozenInstanceError):
            answer.text = "outro"  # type: ignore[misc]

    def test_nao_permite_reatribuir_out_of_scope(self) -> None:
        answer = _make_answer()
        with pytest.raises(FrozenInstanceError):
            answer.out_of_scope = True  # type: ignore[misc]

    def test_nao_permite_reatribuir_tokens(self) -> None:
        answer = _make_answer()
        with pytest.raises(FrozenInstanceError):
            answer.prompt_tokens = 999  # type: ignore[misc]

    def test_frozen_permite_igualdade_por_valor(self) -> None:
        # `frozen=True` também gera __eq__/__hash__ por valor: duas Answer com os
        # mesmos campos são iguais e hasháveis.
        a = _make_answer()
        b = _make_answer()
        assert a == b
        assert hash(a) == hash(b)
