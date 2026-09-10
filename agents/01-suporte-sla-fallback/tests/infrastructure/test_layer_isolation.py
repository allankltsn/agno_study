"""Teste estático de isolamento de camadas — Property 6: Isolamento do domínio.

Este módulo cobre a **Property 6 (Isolamento do domínio)** do design (Validates:
Requirements 12.5, 12.6): as camadas `domain/` e `application/` são Python puro e
**não** podem depender do framework Agno nem dos SDKs de provider (`openai`,
`groq`). Todo tipo da Agno fica confinado à camada `infrastructure` (12.6), e uma
importação proibida nessas camadas deve **falhar a verificação de arquitetura
nomeando o módulo infrator** (12.5).

Estratégia (por que estático, não property-based no sentido de `hypothesis`):
- A propriedade "nenhum módulo de `domain/`/`application/` importa `agno`,
  `openai` ou `groq`" é uma invariante estrutural sobre o código-fonte, não sobre
  entradas de runtime. Verificamo-la por **inspeção estática de imports**: varremos
  todos os arquivos `.py` das duas camadas e analisamos a AST de cada um.
- Usar a AST (em vez de uma varredura textual ingênua) evita falsos positivos com
  a palavra "agno" aparecendo em comentários, docstrings ou strings — só contam
  declarações de import reais.
- Cobrimos as duas formas de import e submódulos:
    - `import agno` / `import agno.models` / `import openai` / `import groq`;
    - `from agno import ...` / `from agno.models.groq import Groq` / etc.
  A checagem é por **raiz do módulo** (primeiro segmento pontilhado), então
  qualquer submódulo (`agno.models.openai`, `openai.types`, ...) também é pego.

Nenhum acesso à rede e nenhuma importação dos módulos sob teste: apenas leitura e
parse dos arquivos-fonte.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Tuple

import pytest

# Raízes de módulo proibidas nas camadas puras (domain/application).
# A checagem é feita sobre o **primeiro segmento** do caminho pontilhado, de modo
# que submódulos como `agno.models.groq` ou `openai.types` também são detectados.
FORBIDDEN_ROOTS = frozenset({"agno", "openai", "groq"})

# Camadas que devem permanecer livres de dependências de framework/SDK.
GUARDED_LAYERS = ("domain", "application")


def _src_root() -> Path:
    """Retorna o diretório `src/suporte/` do projeto a partir deste arquivo de teste.

    Layout: `<projeto>/tests/infrastructure/test_layer_isolation.py` e
    `<projeto>/src/suporte/`. Subimos dois níveis (infrastructure -> tests ->
    projeto) e descemos em `src/suporte`.
    """

    project_dir = Path(__file__).resolve().parents[2]
    return project_dir / "src" / "suporte"


def _iter_layer_files() -> Iterator[Path]:
    """Gera todos os arquivos `.py` sob `domain/` e `application/`.

    Ignora artefatos de cache (`__pycache__`), que não são código-fonte.
    """

    root = _src_root()
    for layer in GUARDED_LAYERS:
        layer_dir = root / layer
        if not layer_dir.exists():
            continue
        for py_file in sorted(layer_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            yield py_file


def _imported_roots(source: str) -> Iterator[str]:
    """Extrai as raízes de módulo importadas em `source` via AST.

    Cobre ambas as formas de import:
    - `import agno.models` -> raiz `agno`;
    - `from agno.models.groq import Groq` -> raiz `agno`.

    Imports relativos (`from . import x`) têm `node.module` possivelmente `None` e
    `level > 0`; são ignorados por não referenciarem pacotes externos.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Import relativo (mesmo pacote) — nunca é um SDK externo.
                continue
            if node.module:
                yield node.module.split(".", 1)[0]


def _forbidden_imports_in(py_file: Path) -> List[str]:
    """Lista as raízes proibidas importadas por `py_file` (vazia se nenhuma)."""

    source = py_file.read_text(encoding="utf-8")
    return sorted({root for root in _imported_roots(source) if root in FORBIDDEN_ROOTS})


def test_ha_arquivos_para_verificar() -> None:
    """Sanidade: a varredura encontra ao menos um arquivo em cada camada.

    Sem isto, um erro de caminho poderia fazer o teste "passar" por vacuidade
    (nenhum arquivo escaneado => nenhuma violação), mascarando uma regressão.
    """

    files = list(_iter_layer_files())
    assert files, "Nenhum arquivo .py encontrado em domain/ ou application/"

    layers_com_arquivos = {
        layer
        for layer in GUARDED_LAYERS
        for f in files
        if layer in f.parts
    }
    assert layers_com_arquivos == set(GUARDED_LAYERS), (
        "Esperava arquivos em ambas as camadas guardadas; encontrei apenas: "
        f"{sorted(layers_com_arquivos)}"
    )


def test_dominio_e_aplicacao_nao_importam_agno_nem_sdks() -> None:
    """Property 6: nem `domain/` nem `application/` importam agno/openai/groq.

    Percorre todos os módulos das camadas puras e coleta as violações. Se houver
    qualquer importação proibida, o teste falha **nomeando o módulo infrator** e o
    SDK importado (Requirement 12.5).

    Validates: Requirements 12.5, 12.6
    """

    root = _src_root()
    violacoes: List[Tuple[str, List[str]]] = []

    for py_file in _iter_layer_files():
        forbidden = _forbidden_imports_in(py_file)
        if forbidden:
            # Caminho relativo a src/suporte para uma mensagem legível.
            rel = py_file.relative_to(root).as_posix()
            violacoes.append((rel, forbidden))

    assert not violacoes, (
        "Isolamento de camadas violado (Property 6): módulos de domain/application "
        "não podem importar agno/openai/groq. Módulos infratores:\n"
        + "\n".join(
            f"  - suporte/{modulo} importa: {', '.join(sdks)}"
            for modulo, sdks in violacoes
        )
    )
