"""Pacote raiz do Agente 01 — Suporte com SLA e fallback de provider.

Arquitetura em camadas (dependência sempre para dentro):
`interface` -> `application` -> `domain`; a `infrastructure` implementa as ports
definidas pelo domínio (inversão de dependência). Tipos da Agno nunca vazam para
fora de `infrastructure`.
"""
