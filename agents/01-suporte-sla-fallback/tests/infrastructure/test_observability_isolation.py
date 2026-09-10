"""Teste estático de isolamento de observabilidade — Property 12.

Este módulo cobre a **Property 12 (Endpoints por env; observabilidade só na
infraestrutura)** do design (Validates: Requirements 15.5, 16.1, 16.8): toda a
configuração de observabilidade — tracing OpenTelemetry, `configure_agno_logging`
e o exporter OTLP — reside **exclusivamente** na camada `infrastructure` e no
`composition_root`. As camadas `domain/` e `application/` **não** podem referenciar
essa configuração (Req 16.8), de modo que trocar o destino do tracing por env
(`OTEL_EXPORTER_OTLP_ENDPOINT`) não altera o código do agente (Req 15.5, 16.1).

Estratégia (por que estático, análogo ao `test_layer_isolation.py` da Property 6,
sem duplicá-lo):
- A propriedade "nenhum módulo de `domain/`/`application/` referencia tracing,
  `configure_agno_logging` ou o exporter OTLP" é uma invariante **estrutural** sobre
  o código-fonte, não sobre entradas de runtime. Verificamo-la por **inspeção
  estática de imports** via AST.
- Usar a AST (em vez de varredura textual ingênua) evita falsos positivos com as
  palavras "tracing"/"otel" aparecendo em comentários, docstrings ou strings — só
  contam declarações de import reais.
- Detectamos três famílias de violação nos imports dessas camadas:
    1. o módulo de tracing da própria infraestrutura
       (`suporte.infrastructure.observability.tracing`);
    2. o SDK/exporter OpenTelemetry (`opentelemetry`, incluindo o exporter OTLP
       `opentelemetry.exporter.otlp`);
    3. símbolos específicos de observabilidade importados por nome —
       `configure_agno_logging`, `init_tracing`, `set_request_id_on_span`.
  As duas formas de import são cobertas (`import X` e `from X import y`), e a
  checagem de pacote é por **prefixo pontilhado**, então submódulos também são pegos.

Enquanto o `test_layer_isolation.py` guarda contra o framework/SDKs de provider
(`agno`/`openai`/`groq`), este teste guarda especificamente a **observabilidade** —
propriedades distintas, requisitos distintos, sem sobreposição.

Nenhum acesso à rede e nenhuma importação dos módulos sob teste: apenas leitura e
parse dos arquivos-fonte.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Tuple

# Módulo de observabilidade da infraestrutura que jamais deve ser importado pelas
# camadas puras. A checagem é por **prefixo pontilhado**, cobrindo submódulos.
FORBIDDEN_MODULE_PREFIXES = (
    ("suporte", "infrastructure", "observability", "tracing"),
    # SDK/exporter OpenTelemetry (o exporter OTLP vive sob opentelemetry.exporter).
    ("opentelemetry",),
)

# Símbolos de observabilidade que, se importados por nome, também caracterizam
# violação — mesmo que o módulo de origem seja reexportado por outro caminho.
FORBIDDEN_SYMBOLS = frozenset(
    {
        "configure_agno_logging",
        "init_tracing",
        "set_request_id_on_span",
    }
)

# Camadas que devem permanecer livres de configuração de observabilidade.
GUARDED_LAYERS = ("domain", "application")


def _src_root() -> Path:
    """Retorna o diretório `src/suporte/` do projeto a partir deste arquivo.

    Layout: `<projeto>/tests/infrastructure/test_observability_isolation.py` e
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


def _module_matches_forbidden(module: str) -> bool:
    """Diz se `module` (caminho pontilhado) cai sob um prefixo proibido.

    A comparação é por segmentos, de modo que `opentelemetry.exporter.otlp` casa
    com o prefixo `("opentelemetry",)` e `suporte.infrastructure.observability.
    tracing` casa exatamente, sem confundir prefixos parciais (p. ex.
    `opentelemetryX` não casaria).
    """

    parts = tuple(module.split("."))
    for prefix in FORBIDDEN_MODULE_PREFIXES:
        if parts[: len(prefix)] == prefix:
            return True
    return False


def _observability_refs(source: str) -> Iterator[str]:
    """Extrai referências de observabilidade proibidas em `source` via AST.

    Cobre ambas as formas de import:
    - `import opentelemetry.trace` / `import suporte.infrastructure.observability.
      tracing` -> reporta o módulo casado;
    - `from opentelemetry import trace` -> reporta o módulo;
    - `from suporte.infrastructure.observability.tracing import init_tracing` ->
      reporta o módulo (e o símbolo, se listado em FORBIDDEN_SYMBOLS);
    - `from x import configure_agno_logging` -> reporta o símbolo mesmo que `x`
      não seja um módulo proibido (defesa contra reexportações).

    Imports relativos (`from . import x`) são ignorados: por convenção do projeto,
    domain/application nunca reexportam a infraestrutura, e o alvo do import
    relativo é resolvido pelo teste da Property 6; aqui focamos em observabilidade
    por caminho absoluto e por nome de símbolo.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_matches_forbidden(alias.name):
                    yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Import relativo (mesmo pacote) — fora do escopo desta propriedade.
                continue
            module = node.module or ""
            if module and _module_matches_forbidden(module):
                yield module
            for alias in node.names:
                if alias.name in FORBIDDEN_SYMBOLS:
                    yield alias.name


def _forbidden_refs_in(py_file: Path) -> List[str]:
    """Lista as referências de observabilidade proibidas em `py_file` (ou vazia)."""

    source = py_file.read_text(encoding="utf-8")
    return sorted(set(_observability_refs(source)))


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


def test_dominio_e_aplicacao_nao_referenciam_observabilidade() -> None:
    """Property 12: domain/application não configuram observabilidade.

    Percorre todos os módulos das camadas puras e coleta as violações. Se algum
    referenciar o módulo de tracing, o SDK/exporter OTLP OpenTelemetry, ou os
    símbolos `configure_agno_logging` / `init_tracing` / `set_request_id_on_span`,
    o teste falha **nomeando o módulo infrator** e a referência proibida — toda a
    observabilidade deve residir só em `infrastructure`/`composition_root`.

    Validates: Requirements 15.5, 16.1, 16.8
    """

    root = _src_root()
    violacoes: List[Tuple[str, List[str]]] = []

    for py_file in _iter_layer_files():
        forbidden = _forbidden_refs_in(py_file)
        if forbidden:
            rel = py_file.relative_to(root).as_posix()
            violacoes.append((rel, forbidden))

    assert not violacoes, (
        "Isolamento de observabilidade violado (Property 12): módulos de "
        "domain/application não podem referenciar tracing, configure_agno_logging "
        "nem o exporter OTLP — a observabilidade reside só em "
        "infrastructure/composition_root. Módulos infratores:\n"
        + "\n".join(
            f"  - suporte/{modulo} referencia: {', '.join(refs)}"
            for modulo, refs in violacoes
        )
    )
