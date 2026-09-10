---
name: agno-code-quality-tools
description: Ferramentas automaticas de qualidade de codigo para agentes Agno em Python (clean code, SOLID, design patterns) que rodam em ambiente LOCAL sem gasto de tokens de LLM - formatacao/lint (Ruff), tipos (mypy), seguranca (Bandit, pip-audit, gitleaks), arquitetura (import-linter), complexidade (Radon/Xenon), testes/cobertura (pytest-cov, Hypothesis, mutmut) e orquestracao (pre-commit, commitizen, CI). Use ao configurar, revisar ou justificar o tooling de qualidade de um agente, ou ao rodar essas verificacoes localmente.
---

# Ferramentas de Qualidade de Codigo (local, sem token)

Esta skill define o conjunto de ferramentas DETERMINISTICAS que sustentam clean
code, SOLID e design patterns nos agentes Agno deste workspace. Todas rodam em
ambiente local (e no CI) sem consumir tokens de LLM.

## Principio: empurre a verificacao para ferramentas deterministicas

O LLM (o agente que escreve/revisa codigo) e caro e nao-deterministico. Toda
verificacao que puder ser feita por uma ferramenta ratapida e reproduzivel DEVE
sair do LLM e entrar num gate automatico (pre-commit local + CI). Isso:

- reduz custo de token (o LLM nao precisa "reler" o codigo para achar erro mecanico);
- da feedback em segundos, no lugar onde o erro nasce (o commit);
- torna a qualidade objetiva e reproduzivel entre maquinas.

O LLM continua util so para o que exige julgamento: se a abstracao faz sentido,
se o nome comunica intencao, se o design pattern e o certo para o problema.

## Por que RODAR EM AMBIENTE LOCAL (nao so no CI)

O gate local (pre-commit) e o CI sao complementares, mas o local e obrigatorio:

- **Feedback imediato**: o erro aparece antes do commit, nao 5 minutos depois no
  pipeline. Ciclo de correcao muito mais curto.
- **Custo zero de pipeline**: nao gasta minutos de CI nem trava a fila de PRs com
  erros triviais de formatacao/lint.
- **Consistencia entre maquinas**: mesma versao de ferramenta para todos (via
  `pre-commit` que fixa versoes dos hooks e via deps de dev no `pyproject.toml`).
- **Seguranca antes do push**: `gitleaks`/`detect-secrets` impedem que um segredo
  saia da maquina — depois de commitado e enviado, o segredo ja vazou.
- **Rede de seguranca em camadas**: o CI reexecuta tudo para quem burlou o hook
  local (`--no-verify`); o local evita a maioria das idas e voltas.

Regra: o CI e a autoridade final (bloqueia merge), mas o desenvolvedor roda o
mesmo conjunto localmente ANTES de commitar. Mesma configuracao nos dois lados.

## As ferramentas por categoria (mapeadas a boas praticas)

### 1. Formatacao e lint — consistencia e estilo

- **Ruff** (`ruff check` + `ruff format`): linter + formatter unico, em Rust,
  muito rapido. Substitui Flake8, isort, pyupgrade, autoflake e cobre o papel do
  Black. E o padrao atual do ecossistema Python. Pega imports nao usados, ordem de
  import, variaveis mortas, antipadroes.
- Trade-off: se a equipe ja padronizou no **Black**, use o formatter do Ruff em
  modo compativel (ambos convergem) e evite rodar os dois.

### 2. Tipos — contratos explicitos (base para SOLID)

- **mypy**: checagem estatica de tipos. Pega uma classe de bugs que o lint nao ve
  (`None` inesperado, contrato de interface violado). Com `typing.Protocol`
  tipado, o mypy vira o guardiao dos contratos das *ports* (SOLID: DIP/LSP/ISP).
- Alternativas: **Pyright** (rapido, base do Pylance) e **ty** (Astral, novo, mesma
  familia do Ruff — ainda maturando).

### 3. Seguranca — SAST e dependencias

- **Bandit**: SAST para Python. Detecta `eval`, `subprocess` sem cuidado,
  desserializacao insegura, segredos hardcoded.
- **pip-audit**: varre dependencias instaladas contra CVEs conhecidas.
- **gitleaks** ou **detect-secrets**: impedem commit de segredos (chaves de API,
  tokens). Conecta direto com a regra "nunca exponha segredos" e com a skill
  `agno-secrets`.
- **Dependabot**/**Renovate** (no repo remoto): PRs automaticos de atualizacao de
  deps. Fora do gate local, mas parte da estrategia.

### 4. Arquitetura — impoe as camadas (o guardiao do SOLID)

- **import-linter**: valida regras de arquitetura em Python. Ex.: proibir que
  `domain` importe `infrastructure`, garantindo a Inversao de Dependencia da
  skill `agno-project-architecture-solid`. E o unico jeito automatico de impedir
  que a separacao de camadas se degrade com o tempo.
- **pydeps**: gera o grafo de dependencias entre modulos; ajuda a enxergar
  acoplamento (uso pontual, nao gate).

### 5. Complexidade — funcoes coesas e curtas

- **Radon**: mede complexidade ciclomatica e indice de manutenibilidade.
- **Xenon**: usa o Radon para FALHAR o build quando um bloco passa de um limite
  (ex.: complexidade > B). Ataca diretamente funcoes longas e mal fatoradas
  (violacoes de SRP).

### 6. Testes e cobertura — testabilidade

- **pytest** + **pytest-cov**/`coverage.py`: suite e cobertura como gate (ex.:
  falhar abaixo de um minimo). Ja em uso neste workspace.
- **Hypothesis**: property-based testing (ja em uso no agente 01). Testa
  invariantes em vez de exemplos fixos.
- **mutmut**/**mutpy** (mutation testing): mede a QUALIDADE dos testes (muda o
  codigo de proposito e verifica se algum teste quebra). Uso periodico, fora do
  gate rapido — e lento.

### 7. Orquestracao — amarra tudo, sem token

- **pre-commit** (framework): roda ruff, mypy, bandit, gitleaks a cada commit.
  Equivalente Python do Husky. Fixa a versao de cada hook (reproduzivel).
- **CI (GitHub Actions/GitLab CI)**: reexecuta a suite completa no PR. Autoridade
  final que bloqueia merge.
- **commitizen** (+ commitlint): valida Conventional Commits e automatiza bump de
  SemVer + geracao de CHANGELOG. Implementa a disciplina de versionamento descrita
  na secao "Versionamento" abaixo (fonte de verdade destes padroes no workspace).
- **tox**/**nox**: matriz de ambientes/versoes; casa com "um projeto uv por agente".

## Stack minima recomendada (custo-beneficio)

Para os agentes deste workspace (Python, uv, layout `src/` em camadas, Hypothesis):

| Boa pratica        | Ferramenta                                  | Gate       |
| ------------------ | ------------------------------------------- | ---------- |
| Formatacao/lint    | Ruff (`ruff check` + `ruff format`)         | pre-commit |
| Tipos              | mypy                                        | pre-commit |
| Seguranca (codigo) | Bandit                                      | pre-commit |
| Seguranca (deps)   | pip-audit                                   | CI         |
| Segredos           | gitleaks (ou detect-secrets)                | pre-commit |
| Arquitetura        | import-linter                               | pre-commit |
| Complexidade       | Radon/Xenon                                 | CI (op.)   |
| Testes/cobertura   | pytest + pytest-cov + Hypothesis            | CI         |
| Qualidade de teste | mutmut                                      | periodico  |
| Versionamento      | commitizen (Conv. Commits + SemVer + CHLOG) | pre-commit |
| Orquestracao       | pre-commit (local) + GitHub Actions (CI)    | ambos      |

Comece com **Ruff + mypy + pre-commit** e adicione o resto conforme a dor aparece
(YAGNI aplicado ao proprio tooling). Ferramenta que so avisa e ninguem olha vira
ruido — o valor esta em configurar o GATE (falhar o commit/CI).

## Configuracao (centralizada no pyproject.toml)

As ferramentas Python leem config do `pyproject.toml` do agente. Exemplo alinhado
ao layout `src/<pacote>` em camadas:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "hypothesis>=6.0",
    "ruff>=0.6",
    "mypy>=1.11",
    "bandit>=1.7",
    "pip-audit>=2.7",
    "import-linter>=2.0",
    "radon>=6.0",
    "xenon>=0.9",
    "pre-commit>=3.8",
    "commitizen>=3.29",
]

[tool.ruff]
src = ["src"]
line-length = 100
target-version = "py39"

[tool.ruff.lint]
# E/F: pyflakes+pycodestyle; I: isort; UP: pyupgrade; B: bugbear; S: bandit-like
select = ["E", "F", "I", "UP", "B", "S", "C90"]
# S101 (assert) liberado em testes via per-file-ignores abaixo.

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.mypy]
python_version = "3.9"
packages = ["suporte"]
mypy_path = "src"
warn_unused_ignores = true
disallow_untyped_defs = true
# Tipos da Agno ficam confinados a infrastructure; ignore libs sem stubs:
[[tool.mypy.overrides]]
module = ["agno.*", "mangum.*"]
ignore_missing_imports = true

[tool.bandit]
exclude_dirs = ["tests"]

[tool.importlinter]
root_package = "suporte"

[[tool.importlinter.contracts]]
name = "Camadas respeitam a direcao de dependencia (domain nao importa infra)"
type = "layers"
layers = [
    "suporte.interface",
    "suporte.application",
    "suporte.domain",
]
# infrastructure e implementacao concreta injetada no composition_root;
# a regra central e: domain/application nao dependem de interface/infrastructure.

[tool.commitizen]
name = "cz_conventional_commits"
version = "0.1.0"
tag_format = "v$version"
update_changelog_on_bump = true
```

O `.pre-commit-config.yaml` e o `pyproject.toml` base ficam versionados em
`scripts/` na raiz do workspace (codigos de uso geral) e sao aplicados por agente
via `scripts/setup_code_quality.sh`. Ver a secao seguinte.

## Uso local (comandos)

Rode a partir da pasta do agente (`agents/<nome>/`):

```bash
# Instala deps de dev (uv) e ativa os hooks locais uma vez
uv sync --group dev
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg   # valida Conventional Commits

# Rodar tudo manualmente (sem esperar o commit)
uv run pre-commit run --all-files

# Ferramentas individuais
uv run ruff check . --fix
uv run ruff format .
uv run mypy src
uv run bandit -r src
uv run pip-audit
uv run lint-imports          # import-linter
uv run xenon --max-absolute B --max-modules A --max-average A src
uv run pytest -m "not e2e and not system and not integration" --cov=suporte
```

O gate rapido de PR reaproveita os marcadores ja definidos no `pyproject.toml`
(`e2e`, `system`, `integration` ficam de fora — ver skill `agno-testing-strategy`).

## Fluxo recomendado

1. Configure o agente com `scripts/setup_code_quality.sh <caminho-do-agente>`.
2. `uv run pre-commit install` (uma vez por clone).
3. Codifique; ao commitar, os hooks rodam Ruff/mypy/Bandit/gitleaks/import-linter.
4. Corrija o que o gate apontar antes de o commit fechar.
5. O CI reexecuta o mesmo conjunto + cobertura no PR (autoridade final).

## Versionamento (Conventional Commits + SemVer + Keep a Changelog)

Esta secao e a FONTE DE VERDADE dos padroes de versionamento do workspace. O hook
`changelog-semver-maintainer` reforca esta disciplina a cada turno e o
**commitizen** a automatiza (validacao de commit, bump e geracao de CHANGELOG).
Aplique quando a mudanca for relevante ao usuario/consumidor.

### 1. CHANGELOG.md — formato Keep a Changelog

- Registre mudancas sob a secao `## [Unreleased]` nas categorias: **Added**,
  **Changed**, **Deprecated**, **Removed**, **Fixed**, **Security**.
- Cada entrada e uma linha concisa, orientada ao IMPACTO para quem consome o
  software (nao ao detalhe interno de implementacao).
- Ao publicar uma release, mova o conteudo de `[Unreleased]` para
  `## [X.Y.Z] - AAAA-MM-DD`.

### 2. Conventional Commits — mensagens padronizadas

Formato: `tipo(escopo opcional): descricao`. Tipos comuns: `feat`, `fix`, `perf`,
`refactor`, `docs`, `test`, `build`, `ci`, `chore`. Breaking change: `!` apos o
tipo (`feat!:`) ou `BREAKING CHANGE:` no corpo. E a base da qual o SemVer e derivado.

### 3. Semantic Versioning (X.Y.Z) — derivado dos commits desde a ultima tag

- **MAJOR (X)**: breaking change — commit com `BREAKING CHANGE:` no corpo ou `!`
  apos o tipo (ex.: `feat!:`).
- **MINOR (Y)**: nova funcionalidade retrocompativel — commits `feat`.
- **PATCH (Z)**: correcoes e ajustes retrocompativeis — `fix`, `perf` e afins.
- **Antes de 1.0.0**: trate breaking changes como MINOR e features como PATCH,
  sinalizando instabilidade.

### 4. Fluxo pratico ao concluir uma mudanca relevante

1. Atualize/append no CHANGELOG (secao `[Unreleased]`).
2. Calcule e proponha o proximo numero SemVer com uma justificativa de 1 linha.
3. Se o usuario aprovar a release, sugira a tag anotada
   (`git tag -a vX.Y.Z -m ...`) — nao crie a tag automaticamente sem confirmacao.

Nao infle o changelog com ruido interno (refactors puros, formatacao) a menos que
afetem o consumidor. Seja um curador criterioso do historico. A config do
commitizen (`[tool.commitizen]`, ver secao de Configuracao) automatiza o bump e a
geracao do CHANGELOG a partir destes mesmos padroes.

## Relacao com outras skills

- `agno-project-architecture-solid`: define as camadas que o **import-linter** faz
  cumprir automaticamente.
- `agno-testing-strategy`: define as camadas de teste, marcadores e o eval; esta
  skill cobre o tooling que roda a suite (pytest-cov, Hypothesis, mutmut).
- `agno-secrets`/`agno-security`: **gitleaks**/**Bandit**/**pip-audit** sao a
  contraparte automatica das regras de seguranca.
- `agno-project-structure`: a pasta `scripts/` (codigos de uso geral) mora na raiz
  do workspace.

## Checklist

- [ ] Ruff (lint + format) configurado no `pyproject.toml` e no pre-commit.
- [ ] mypy cobrindo `src/` com libs sem stub ignoradas explicitamente.
- [ ] Bandit + pip-audit rodando (codigo e dependencias).
- [ ] gitleaks/detect-secrets bloqueando segredos antes do commit.
- [ ] import-linter garantindo a direcao de dependencia das camadas.
- [ ] Complexidade sob limite (Ruff C90 e/ou Xenon).
- [ ] pytest + cobertura como gate no CI; Hypothesis nos invariantes.
- [ ] pre-commit instalado localmente; CI reexecuta o mesmo conjunto.
- [ ] commitizen validando Conventional Commits e bump de SemVer + CHANGELOG.
