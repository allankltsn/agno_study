# scripts/

Codigos base de uso geral do workspace — utilitarios reaproveitaveis entre os
agentes Agno. Sao scripts e templates deterministicos, sem logica de negocio de
nenhum agente especifico.

## Conteudo

| Arquivo                     | Proposito                                                            |
| --------------------------- | ------------------------------------------------------------------- |
| `setup_code_quality.sh`     | Instala e configura o tooling de qualidade de codigo num agente.    |
| `templates/pyproject.quality.toml` | Bloco base de config das ferramentas (Ruff, mypy, Bandit, etc). |
| `templates/.pre-commit-config.yaml` | Hooks pre-commit (local, sem gasto de token de LLM).       |
| `templates/import-linter.toml` | Contrato de arquitetura em camadas (import-linter).             |

## Como usar

Configurar o tooling de qualidade num agente existente:

```bash
# a partir da raiz do workspace
bash scripts/setup_code_quality.sh agents/01-suporte-sla-fallback
```

O script e idempotente: copia os templates que faltam, adiciona as deps de dev
via `uv`, instala os hooks do pre-commit e roda uma verificacao inicial.

## Por que rodar localmente

Estas ferramentas rodam em ambiente LOCAL (pre-commit) e no CI, sem consumir
tokens de LLM. O detalhamento de cada ferramenta, o mapeamento com clean
code/SOLID e a justificativa do gate local estao na skill
`.kiro/skills/agno-code-quality-tools/SKILL.md`.
