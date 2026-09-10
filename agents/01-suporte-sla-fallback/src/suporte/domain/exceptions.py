"""Erros de domínio do agente de suporte.

Este módulo concentra as exceções que representam falhas *de negócio* — não de
framework. Elas nascem nas camadas internas (domínio / aplicação / adapters) e são
traduzidas em mensagens amigáveis na `interface` (mensagem na CLI, HTTP ``502`` na API),
sempre **sem** vazar detalhes de infraestrutura nem stacktrace ao usuário final.

Manter esses erros aqui, em Python puro, garante que o domínio permaneça independente da
Agno e dos SDKs de provider (DIP): quem captura `ProviderUnavailable` ou
`OutOfScopeQuestion` não precisa conhecer *como* a resposta foi obtida.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base de todos os erros de domínio.

    Existe para que a `interface` possa capturar `DomainError` de forma abrangente e
    traduzi-lo em uma mensagem amigável, sem depender das subclasses concretas nem de
    exceções específicas de framework.
    """


class ProviderUnavailable(DomainError):
    """Nenhum provider conseguiu responder dentro do SLA.

    É levantado pelo `FallbackLLM` quando **todos** os providers da `provider_order`
    falharam ou excederam o timeout em todas as tentativas configuradas (após retry com
    backoff e fallback entre providers). Sinaliza a falha total de disponibilidade
    (RNF-02) e habilita a degradação graciosa na camada de aplicação.

    Attributes:
        attempts: Nomes dos providers tentados, na **ordem** em que foram tentados.
            Preserva a sequência para diagnóstico e log estruturado (`result="error"`).
    """

    def __init__(
        self,
        attempts: list[str],
        message: str = "Todos os providers falharam",
    ) -> None:
        self.attempts = attempts  # nomes dos providers tentados, na ordem
        super().__init__(message)


class OutOfScopeQuestion(DomainError):
    """A pergunta está fora do escopo conhecido do produto.

    Nasce quando o modelo sinaliza que sua resposta não está fundamentada na documentação
    fornecida (RF-03). Em vez de devolver conteúdo possivelmente alucinado, o fluxo deve
    encaminhar a pergunta a um atendente humano.
    """
