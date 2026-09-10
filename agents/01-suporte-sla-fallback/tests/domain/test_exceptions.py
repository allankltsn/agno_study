"""Testes de unidade dos erros de domínio (domain/exceptions.py).

Foco desta tarefa: garantir que `ProviderUnavailable.attempts` preserva a **ordem**
em que os providers foram tentados (Req 5.5) — informação usada no diagnóstico e no
log estruturado (`result="error"`). Verifica também a hierarquia de `DomainError` e
a mensagem padrão.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from suporte.domain.exceptions import (
    DomainError,
    OutOfScopeQuestion,
    ProviderUnavailable,
)


class TestProviderUnavailableAttempts:
    """`attempts` deve preservar a ordem de tentativa dos providers (Req 5.5)."""

    def test_attempts_preserva_a_ordem_informada(self) -> None:
        attempts = ["openai", "groq", "ollama"]
        exc = ProviderUnavailable(attempts)
        assert exc.attempts == ["openai", "groq", "ollama"]

    def test_attempts_ordem_diferente_e_preservada(self) -> None:
        exc = ProviderUnavailable(["groq", "openai"])
        assert exc.attempts == ["groq", "openai"]

    def test_attempts_lista_com_um_provider(self) -> None:
        exc = ProviderUnavailable(["openai"])
        assert exc.attempts == ["openai"]

    def test_mensagem_padrao(self) -> None:
        exc = ProviderUnavailable(["openai", "groq"])
        assert str(exc) == "Todos os providers falharam"

    def test_mensagem_customizada(self) -> None:
        exc = ProviderUnavailable(["openai"], message="SLA total estourado")
        assert str(exc) == "SLA total estourado"

    @given(
        attempts=st.lists(
            st.sampled_from(["openai", "groq", "ollama", "azure", "anthropic"]),
            min_size=1,
            max_size=10,
        )
    )
    def test_attempts_preserva_ordem_para_qualquer_sequencia(
        self, attempts: list
    ) -> None:
        """A ordem armazenada em `attempts` é idêntica à ordem recebida.

        **Validates: Requirements 5.5**
        """
        exc = ProviderUnavailable(list(attempts))
        assert exc.attempts == attempts


class TestDomainErrorHierarquia:
    """Os erros de domínio herdam de `DomainError` para captura abrangente."""

    def test_provider_unavailable_e_domain_error(self) -> None:
        assert issubclass(ProviderUnavailable, DomainError)

    def test_out_of_scope_e_domain_error(self) -> None:
        assert issubclass(OutOfScopeQuestion, DomainError)

    def test_provider_unavailable_capturavel_como_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise ProviderUnavailable(["openai"])

    def test_domain_error_e_exception(self) -> None:
        assert issubclass(DomainError, Exception)
