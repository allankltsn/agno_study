# syntax=docker/dockerfile:1
#
# Dockerfile — caminho de deploy **ALTERNATIVO** da app FastAPI (Req 14.5).
#
# ------------------------------------------------------------------------------
# Quando usar este caminho (container + AWS Lambda Web Adapter):
#
#   - Use o caminho PADRÃO (Mangum + Lambda Layer, via `template.yaml`/SAM e
#     `interface/api/main.py` com `handler = Mangum(app)`) sempre que possível: é
#     mais simples e é o default do projeto.
#
#   - Use ESTE caminho (imagem de container) quando o pacote de deploy exceder o
#     limite de Lambda Layers (250 MB descompactado). A mesma app FastAPI sobe com
#     `uvicorn` e o AWS Lambda Web Adapter faz a ponte HTTP <-> Lambda, suportando
#     inclusive streaming real. Imagens de container do Lambda toleram artefatos
#     bem maiores (até 10 GB), contornando o limite de Layers.
#
# Ponto-chave (Req 14.4, 14.5): a app FastAPI é IDÊNTICA nos dois caminhos. Aqui
# NÃO usamos Mangum — apontamos o `uvicorn` para a MESMA `app`
# (`suporte.interface.api.api:app`). Não há redefinição de rotas nem de validação.
#
# Este arquivo é INFRAESTRUTURA: cria a receita da imagem. O build/push/deploy é
# feito à parte (não é executado por este projeto de estudo).
# ------------------------------------------------------------------------------

# Imagem base Python enxuta. A tag "slim" reduz o tamanho da imagem final sem
# perder o que o runtime precisa. Fixamos a minor 3.12 para builds reproduzíveis.
FROM python:3.12-slim

# --- AWS Lambda Web Adapter --------------------------------------------------
# Copia a extension do Web Adapter para /opt/extensions. O Lambda carrega tudo em
# /opt/extensions automaticamente; a extension escuta o runtime API do Lambda e
# encaminha cada invocação como uma requisição HTTP para a app local (uvicorn),
# devolvendo a resposta HTTP. É o que permite rodar uma app HTTP "normal" no Lambda.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter

# --- Configuração do adapter -------------------------------------------------
# Porta em que a app HTTP escuta dentro do container; o Web Adapter envia as
# requisições para essa porta. `PORT` é a env padrão lida pelo adapter, e
# `AWS_LWA_PORT` é o alias explícito — definimos ambas para deixar claro o contrato.
ENV PORT=8080 \
    AWS_LWA_PORT=8080 \
    # Não gera .pyc e não bufferiza stdout/stderr — logs saem na hora (útil no
    # CloudWatch) e a imagem fica um pouco mais limpa.
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# --- Dependências ------------------------------------------------------------
# Instala o gerenciador `uv` (mesma ferramenta usada no desenvolvimento local) a
# partir da imagem oficial, copiando apenas os binários. É rápido e determinístico.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copiamos primeiro os metadados de dependências para aproveitar o cache de camadas
# do Docker: enquanto `pyproject.toml`/`uv.lock` não mudarem, a instalação não é
# refeita a cada alteração de código-fonte.
COPY pyproject.toml uv.lock README.md ./

# `--frozen`: respeita o `uv.lock` (build reproduzível, sem resolver de novo).
# `--no-dev`: instala só as dependências de runtime (fastapi, uvicorn, agno, etc.),
# deixando de fora o grupo `dev` (pytest, hypothesis, httpx). O `uvicorn` é
# adicionado explicitamente porque no caminho padrão quem sobe a app é o Mangum;
# aqui o servidor ASGI é o próprio uvicorn.
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install "uvicorn[standard]>=0.30,<1.0"

# Agora sim o código-fonte da aplicação. Fica numa camada separada para que
# mudanças de código não invalidem o cache de dependências acima.
COPY src ./src

# `uv` cria o ambiente virtual em /app/.venv; colocamos seus binários no PATH para
# poder chamar `uvicorn` direto no CMD.
ENV PATH="/app/.venv/bin:$PATH" \
    # `src` no PYTHONPATH permite importar o pacote `suporte` sem instalar o projeto.
    PYTHONPATH="/app/src"

# --- Entrypoint --------------------------------------------------------------
# Sobe a MESMA app FastAPI (`suporte.interface.api.api:app`) com uvicorn, escutando
# em 0.0.0.0:8080. `--host 0.0.0.0` é obrigatório para que o Web Adapter (rodando
# no mesmo container) consiga alcançar o servidor. Sem Mangum: no caminho de
# container, a ponte com o Lambda é responsabilidade do Web Adapter (Req 14.5).
CMD ["uvicorn", "suporte.interface.api.api:app", "--host", "0.0.0.0", "--port", "8080"]
