"""Teste estático de segurança — Property 8: Segredos fora do código.

Este teste verifica, por **inspeção estática** do repositório do agente, o
contrato de segredos do Requisito 10 (RNF-04):

- **Req 10.1** — nenhum *literal* de chave de API aparece no código-fonte;
  as chaves são lidas somente de variáveis de ambiente em tempo de execução.
- **Req 10.4** — o projeto fornece um arquivo `.env.example` presente na raiz,
  contendo apenas placeholders/valores vazios (sem valores reais de chave).
- **Req 10.3** — o `.env` está listado no `.gitignore`, de modo que segredos
  reais nunca sejam versionados.

**Validates: Requirements 10.1, 10.3, 10.4**

Estratégia (sem rede, sem framework): varremos os arquivos `.py` sob `src/` em
busca de padrões de **valor** de chave de API (prefixos conhecidos como
``sk-``/``gsk_`` seguidos de um corpo longo). É importante distinguir o *valor*
de uma chave (proibido) do *nome* de uma variável de ambiente (permitido): o
código legitimamente referencia nomes como ``OPENAI_API_KEY``/``GROQ_API_KEY``,
que NÃO são segredos. Por isso os padrões abaixo casam apenas com o formato
típico de um segredo literal, nunca com identificadores de env var.

O teste é determinístico e resolve os caminhos relativos a este arquivo, sem
depender do diretório de trabalho corrente.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Pattern

# Raiz do projeto do agente (…/agents/01-suporte-sla-fallback), derivada a partir
# deste arquivo: tests/infrastructure/ -> tests/ -> raiz do projeto.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"

# Padrões que denotam um *valor* de chave de API literal embutido no código.
# Cada padrão exige um corpo longo o suficiente para caracterizar um segredo real,
# evitando falsos positivos com nomes de variáveis ou trechos curtos.
#   - OpenAI: chaves começam com "sk-" seguidas de um token longo.
#   - Groq:   chaves começam com "gsk_" seguidas de um token longo.
# Também cobrimos genericamente atribuições que colocam um literal não vazio numa
# variável cujo nome sugere ser a própria chave (e não o *nome* da env var).
_SECRET_VALUE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gsk_[A-Za-z0-9_\-]{16,}"),
]

# Atribuição direta de um literal a algo que parece a chave em si, por exemplo
# ``api_key = "algum-valor"``. Permitimos explicitamente o caso do *nome* da env
# var (``api_key_env = "OPENAI_API_KEY"``) e valores vazios (``api_key = ""``).
_HARDCODED_KEY_ASSIGNMENT = re.compile(
    r"""(?ix)                 # case-insensitive, verbose
    \b(?:api_key|apikey|secret_key|access_key)\b   # nome da variável (sem _env)
    \s*=\s*
    ["']([^"']+)["']          # captura o valor literal atribuído
    """
)


def _iter_source_files() -> List[Path]:
    """Retorna todos os arquivos `.py` sob `src/`, ignorando caches.

    Excluímos diretórios de bytecode (`__pycache__`) para varrer apenas o
    código-fonte de fato mantido no repositório.
    """

    return [
        path
        for path in SRC_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_src_dir_exists() -> None:
    """Garante que a varredura tem alvo: o diretório `src/` deve existir.

    Sem esta âncora, uma mudança de layout poderia esvaziar a varredura e
    mascarar a ausência das demais asserções (Req 10.1).
    """

    assert SRC_DIR.is_dir(), f"Diretório de fontes não encontrado: {SRC_DIR}"
    assert _iter_source_files(), "Nenhum arquivo .py encontrado sob src/."


def test_nenhum_literal_de_chave_no_codigo_fonte() -> None:
    """Req 10.1 — nenhum literal de chave de API aparece no código-fonte.

    Varremos cada `.py` sob `src/` e falhamos nomeando o arquivo e a linha
    infratora caso um padrão de *valor* de chave (ex.: ``sk-…``) seja encontrado.
    Referências ao *nome* da env var (ex.: ``OPENAI_API_KEY``) são permitidas e
    não casam com os padrões.
    """

    offenders: List[str] = []

    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in _SECRET_VALUE_PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(PROJECT_ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Literais de chave de API encontrados no código-fonte "
        "(Req 10.1). Mova-os para variáveis de ambiente:\n"
        + "\n".join(offenders)
    )


def test_nenhuma_atribuicao_de_chave_hardcoded() -> None:
    """Req 10.1 — nenhuma variável recebe uma chave literal não vazia.

    Detecta atribuições como ``api_key = "valor"``. O *nome* da env var é
    referenciado via `api_key_env` (que não casa com o padrão) e valores vazios
    (`""`) são explicitamente tolerados, cobrindo o caso legítimo em que a chave
    é lida do ambiente e não embutida no código.
    """

    offenders: List[str] = []

    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _HARDCODED_KEY_ASSIGNMENT.search(line)
            if match is None:
                continue
            value = match.group(1).strip()
            if value:  # valor vazio é permitido; literal não vazio é proibido
                rel = path.relative_to(PROJECT_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Atribuição de chave de API literal encontrada no código-fonte "
        "(Req 10.1):\n" + "\n".join(offenders)
    )


def test_env_example_presente_e_sem_valores_reais() -> None:
    """Req 10.4 — `.env.example` existe na raiz e traz apenas placeholders.

    Além de exigir a presença do arquivo, conferimos que as entradas de chave de
    API (`*_API_KEY`) têm valor **vazio** — sem valores reais versionados. Linhas
    de comentário e outras variáveis (ordem, timeouts) são ignoradas.
    """

    env_example = PROJECT_ROOT / ".env.example"
    assert env_example.is_file(), (
        f".env.example ausente na raiz do projeto: {env_example} (Req 10.4)"
    )

    offenders: List[str] = []
    for lineno, raw in enumerate(
        env_example.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().upper().endswith("_API_KEY") and value.strip():
            offenders.append(f"linha {lineno}: {line}")

    assert not offenders, (
        ".env.example deve conter apenas placeholders/valores vazios para as "
        "chaves de API, sem valores reais (Req 10.4):\n" + "\n".join(offenders)
    )


def test_gitignore_lista_dotenv() -> None:
    """Req 10.3 — `.gitignore` existe e ignora o `.env`.

    Aceitamos a entrada exata `.env` (com possíveis espaços em volta),
    garantindo que o arquivo de segredos reais nunca seja versionado.
    """

    gitignore = PROJECT_ROOT / ".gitignore"
    assert gitignore.is_file(), (
        f".gitignore ausente na raiz do projeto: {gitignore} (Req 10.3)"
    )

    entries = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
    }
    assert ".env" in entries, (
        ".gitignore deve listar '.env' para não versionar segredos (Req 10.3)."
    )
