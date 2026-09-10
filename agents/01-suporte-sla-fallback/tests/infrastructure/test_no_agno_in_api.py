"""Teste estático — Property 10: `interface/api` não importa Agno.

Este módulo cobre a parte de **isolamento estrutural** da **Property 10 (FastAPI
reutiliza o domínio, sem tipos Agno, erro → 502)** do design (Validates:
Requirements 14.1, 14.6, 14.7): a camada `interface/api` reutiliza o caso de uso
`AnswerQuestion` via `composition_root` (14.1) e **não importa tipos da Agno**
(14.6) — os tipos da Agno ficam confinados à `infrastructure`/`agentos`. Uma
importação de `agno` nessa camada deve **falhar a verificação nomeando o módulo
infrator**.

Escopo: complementa (sem duplicar) `test_layer_isolation.py`, que guarda
`domain/` e `application/` contra `agno`/`openai`/`groq`. Aqui o alvo é
especificamente `src/suporte/interface/api/` e a raiz proibida é **apenas**
`agno` — as demais dependências de framework (fastapi, pydantic, mangum) são
legítimas nesta borda HTTP.

Estratégia (por que estático, não `hypothesis`):
- "Nenhum módulo de `interface/api/` importa `agno`" é uma invariante estrutural
  sobre o código-fonte, não sobre entradas de runtime. Verificamo-la por
  **inspeção estática de imports**: varremos todos os arquivos `.py` da camada e
  analisamos a AST de cada um.
- Usar a AST (em vez de varredura textual) evita falsos positivos com a palavra
  "agno" em comentários, docstrings ou strings — só contam imports reais.
- Cobrimos as duas formas de import e submódulos:
    - `import agno` / `import agno.models.groq`;
    - `from agno import ...` / `from agno.models.groq import Groq`.
  A checagem é por **raiz do módulo** (primeiro segmento pontilhado), então
  qualquer submódulo (`agno.models.openai`, ...) também é pego.

Nenhum acesso à rede e nenhuma importação dos módulos sob teste: apenas leitura e
parse dos arquivos-fonte.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Tuple

# Raiz de módulo proibida na camada `interface/api`. A checagem é feita sobre o
# **primeiro segmento** do caminho pontilhado, de modo que submódulos como
# `agno.models.groq` também são detectados. Diferente de `test_layer_isolation`,
# aqui só `agno` é proibida: fastapi/pydantic/mangum são legítimos nesta borda.
FORBIDDEN_ROOT = "agno"

# Camada guardada por este teste: a interface HTTP (FastAPI + handler serverless).
API_LAYER = ("interface", "api")


def _src_root() -> Path:
    """Retorna o diretório `src/suporte/` do projeto a partir deste arquivo de teste.

    Layout: `<projeto>/tests/infrastructure/test_no_agno_in_api.py` e
    `<projeto>/src/suporte/`. Subimos dois níveis (infrastructure -> tests ->
    projeto) e descemos em `src/suporte`.
    """

    project_dir = Path(__file__).resolve().parents[2]
    return project_dir / "src" / "suporte"


def _api_dir() -> Path:
    """Retorna o diretório `src/suporte/interface/api/`."""

    return _src_root().joinpath(*API_LAYER)


def _iter_api_files() -> Iterator[Path]:
    """Gera todos os arquivos `.py` sob `interface/api/`.

    Ignora artefatos de cache (`__pycache__`), que não são código-fonte.
    """

    api_dir = _api_dir()
    if not api_dir.exists():
        return
    for py_file in sorted(api_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        yield py_file


def _imported_roots(source: str) -> Iterator[str]:
    """Extrai as raízes de módulo importadas em `source` via AST.

    Cobre ambas as formas de import:
    - `import agno.models` -> raiz `agno`;
    - `from agno.models.groq import Groq` -> raiz `agno`.

    Imports relativos (`from . import x`) têm `node.level > 0`; são ignorados por
    não referenciarem pacotes externos.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Import relativo (mesmo pacote) — nunca é o framework Agno.
                continue
            if node.module:
                yield node.module.split(".", 1)[0]


def _imports_agno(py_file: Path) -> bool:
    """Indica se `py_file` importa `agno` (em qualquer forma/submódulo)."""

    source = py_file.read_text(encoding="utf-8")
    return any(root == FORBIDDEN_ROOT for root in _imported_roots(source))


def test_ha_arquivos_de_api_para_verificar() -> None:
    """Sanidade: a varredura encontra ao menos um arquivo em `interface/api/`.

    Sem isto, um erro de caminho poderia fazer o teste "passar" por vacuidade
    (nenhum arquivo escaneado => nenhuma violação), mascarando uma regressão.
    """

    files = list(_iter_api_files())
    assert files, (
        "Nenhum arquivo .py encontrado em interface/api/ "
        f"({_api_dir()}); verifique o layout do projeto."
    )


def test_interface_api_nao_importa_agno() -> None:
    """Property 10: nenhum módulo de `interface/api/` importa `agno`.

    Percorre todos os módulos da camada e coleta as violações. Se houver qualquer
    importação de `agno` (ou submódulo), o teste falha **nomeando o módulo
    infrator** (Requirements 14.1, 14.6, 14.7): a borda HTTP reutiliza o domínio
    via `composition_root` e mantém os tipos da Agno confinados à `infrastructure`.

    Validates: Requirements 14.1, 14.6, 14.7
    """

    root = _src_root()
    infratores: List[str] = []

    for py_file in _iter_api_files():
        if _imports_agno(py_file):
            # Caminho relativo a src/suporte para uma mensagem legível.
            infratores.append(py_file.relative_to(root).as_posix())

    assert not infratores, (
        "Isolamento da borda HTTP violado (Property 10): módulos de "
        "interface/api/ não podem importar `agno` — os tipos da Agno ficam "
        "confinados à camada infrastructure. Módulos infratores:\n"
        + "\n".join(f"  - suporte/{modulo}" for modulo in infratores)
    )
