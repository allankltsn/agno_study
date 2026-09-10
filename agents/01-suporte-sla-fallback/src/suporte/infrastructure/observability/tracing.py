"""Tracing OpenTelemetry, `configure_agno_logging` e helpers de `request_id`.

Este módulo concentra **toda** a configuração de observabilidade baseada em
tracing distribuído (OpenTelemetry) e o vínculo com o logger do Agno. Ele é
ligado **exclusivamente** pelo `composition_root` (Req 16.8) — o domínio e a
aplicação nunca o importam, mantendo os tipos de framework/observabilidade
confinados à camada `infrastructure`.

O que este módulo faz:
- `init_tracing(otel_endpoint, *, debug_mode=False)`: configura o exporter OTLP
  para o endpoint informado (vindo de `OTEL_EXPORTER_OTLP_ENDPOINT`). Se o
  endpoint estiver ausente/vazio, **desabilita** o tracing, registra um aviso no
  log estruturado e **continua** a requisição sem interromper (Req 16.1/16.2).
  Também liga `configure_agno_logging(custom_default_logger=..., ...)` para que o
  Agno emita **JSON de uma linha** com estrutura idêntica ao `StructuredLogger`
  (Req 16.3), e habilita o `debug_mode` do Agno **apenas** quando pedido — por
  padrão desligado fora de dev (Req 16.6/16.7).
- `new_request_id()`: gera um `request_id` único por requisição.
- `set_request_id_on_span(request_id)`: anexa o `request_id` como atributo
  `app.request_id` no span OTel atual, correlacionando log e trace (Req 16.4).

Imports preguiçosos e tolerantes a ausência (design):
- OpenTelemetry e Agno são importados **dentro** das funções e protegidos por
  `try/except ImportError`. Assim, o módulo importa limpo mesmo que os pacotes
  não estejam instalados, e o núcleo (CLI) continua funcionando sem eles. Se as
  bibliotecas de tracing não estiverem disponíveis, apenas registramos um aviso e
  seguimos em frente — nunca derrubamos a requisição (Req 16.2).

Privacidade (Req 16.5): este módulo **não** recebe nem manipula o texto da
pergunta nem chaves de API. A pergunta só circula, na observabilidade, como
`question_hash` (produzido no módulo `logging`), e o `request_id` é um
identificador opaco (hex de UUID) sem PII.
"""

import logging
import uuid
from typing import Optional

__all__ = ["init_tracing", "new_request_id", "set_request_id_on_span"]

# Nome do atributo que correlaciona log e trace: o mesmo valor de `request_id`
# gravado no log estruturado é anexado ao span sob esta chave (Req 16.4).
_REQUEST_ID_SPAN_ATTRIBUTE = "app.request_id"

# Logger local do módulo, usado para os avisos de observabilidade (ex.: tracing
# desabilitado por falta de endpoint, ou pacotes OTel/Agno ausentes). Usa o
# mecanismo padrão da stdlib para respeitar os handlers configurados no processo
# — o mesmo destino do log estruturado da aplicação.
_logger = logging.getLogger("suporte.observability.tracing")


def init_tracing(otel_endpoint: Optional[str], *, debug_mode: bool = False) -> None:
    """Inicializa o tracing OTLP e liga o logger JSON do Agno.

    Comportamento (Req 16.1/16.2/16.3/16.6/16.7):
    - Se `otel_endpoint` for ``None`` ou vazio/apenas espaços: **desabilita** o
      tracing, registra um aviso de nível ``warning`` indicando que a exportação
      foi desabilitada por endpoint não configurado, e **retorna** sem levantar
      exceção — a requisição segue normalmente.
    - Caso contrário: configura um exporter OTLP apontando para `otel_endpoint`,
      registrando-o num `TracerProvider` global via um `BatchSpanProcessor`.
    - Sempre tenta ligar `configure_agno_logging(custom_default_logger=..., ...)`
      para que o Agno emita JSON de uma linha com a mesma estrutura de campos do
      `StructuredLogger`, e habilita o `debug_mode` do Agno **apenas** quando
      `debug_mode=True` (por padrão desligado fora de dev).

    Todos os imports de OpenTelemetry/Agno são preguiçosos e protegidos por
    ``try/except ImportError``: se os pacotes não estiverem instalados, apenas
    registramos um aviso e continuamos (o núcleo/CLI não depende deles).

    Args:
        otel_endpoint: Endpoint OTLP (valor de `OTEL_EXPORTER_OTLP_ENDPOINT`).
            ``None``/vazio desabilita a exportação de tracing (Req 16.2).
        debug_mode: Liga o `debug_mode` do Agno (só em dev). Desligado por padrão
            (Req 16.6/16.7).
    """
    # --- Guard de endpoint ausente/vazio (Req 16.2) --------------------------
    # `None`, string vazia ou apenas espaços significam "tracing desabilitado".
    # Não é erro: apenas avisamos e seguimos, para não interromper a requisição.
    if otel_endpoint is None or not otel_endpoint.strip():
        _logger.warning(
            "Tracing OpenTelemetry desabilitado: "
            "OTEL_EXPORTER_OTLP_ENDPOINT ausente ou vazio. "
            "A requisicao prossegue normalmente."
        )
        # Mesmo sem exporter OTLP, ainda tentamos ligar o logger do Agno para
        # manter o formato JSON de uma linha consistente (Req 16.3).
        _configure_agno_logging(debug_mode=debug_mode)
        return

    endpoint = otel_endpoint.strip()

    # --- Configuração do exporter OTLP (Req 16.1) ----------------------------
    # Imports preguiçosos e guardados: se o SDK do OpenTelemetry não estiver
    # instalado, não derrubamos nada — apenas avisamos e seguimos (Req 16.2).
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        _logger.warning(
            "Tracing OpenTelemetry desabilitado: pacotes OTel indisponiveis (%s). "
            "A requisicao prossegue normalmente.",
            exc,
        )
        _configure_agno_logging(debug_mode=debug_mode)
        return

    try:
        # Identifica o serviço nos traces exportados. Nome estável para que os
        # spans sejam agrupados corretamente no backend de observabilidade.
        resource = Resource.create({"service.name": "suporte-sla-fallback"})
        provider = TracerProvider(resource=resource)

        # O exporter fala OTLP com o collector/endpoint configurado. O
        # `BatchSpanProcessor` agrupa spans e exporta em lote (menos overhead).
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        # Registra o provider como global; a partir daqui, `trace.get_tracer` e
        # `trace.get_current_span` usam este provider.
        trace.set_tracer_provider(provider)
        _logger.info(
            "Tracing OpenTelemetry habilitado; exportando via OTLP para o endpoint configurado."
        )
    except Exception as exc:  # pragma: no cover - defensivo
        # Qualquer falha ao montar o pipeline de tracing NÃO deve interromper a
        # requisição (Req 16.2): apenas avisamos e continuamos sem exportação.
        _logger.warning(
            "Falha ao inicializar o tracing OpenTelemetry (%s); "
            "a requisicao prossegue sem exportacao de tracing.",
            exc,
        )

    # Independentemente do exporter, ligamos o logger do Agno (Req 16.3).
    _configure_agno_logging(debug_mode=debug_mode)


def _configure_agno_logging(*, debug_mode: bool) -> None:
    """Liga `configure_agno_logging` ao logger JSON de uma linha (Req 16.3).

    Import preguiçoso e guardado: se o Agno não estiver instalado, apenas
    registramos um aviso e seguimos — o núcleo/CLI não depende do Agno.

    O `debug_mode` do Agno é habilitado **somente** quando `debug_mode=True`
    (dev); por padrão fica desligado (Req 16.6/16.7).

    Nota sobre a API do Agno: `configure_agno_logging` aceita
    `custom_default_logger=<logging.Logger>` para o Agno emitir seus eventos por
    esse logger. Usamos o mesmo logger padrão do `StructuredLogger`
    (``suporte.request``), cujo handler escreve uma única linha por registro, de
    modo que os eventos do Agno saiam como JSON de uma linha com a mesma
    estrutura da aplicação.

    Sobre o `debug_mode` (Req 16.6/16.7): nesta versão do Agno, o nível de
    verbosidade de logging é controlado no próprio `Agent` (parâmetro `debug_mode`
    ao construir o agente), não em `configure_agno_logging`. Aqui, ligar/desligar
    o `debug_mode` ajusta o **nível** do logger customizado do Agno: ``DEBUG`` em
    dev, ``INFO`` (padrão) fora de dev. Assim mantemos "debug só em dev, desligado
    por padrão" de forma coerente com a superfície de logging deste módulo.
    """
    # Ajusta o nível do logger do Agno conforme o modo: DEBUG só em dev,
    # INFO por padrão (Req 16.6/16.7). Isso é aplicado ao logger customizado que
    # entregamos ao Agno abaixo.
    agno_log_level = logging.DEBUG if debug_mode else logging.INFO

    try:
        from agno.utils.log import configure_agno_logging
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        _logger.warning(
            "configure_agno_logging indisponivel (%s); "
            "seguindo sem reconfigurar o logger do Agno.",
            exc,
        )
        return

    # Reutiliza o mesmo logger de destino do log estruturado da aplicação, para
    # que os eventos do Agno saiam com a mesma estrutura/uma linha (Req 16.3).
    try:
        from suporte.infrastructure.observability.logging import _get_default_logger

        json_logger = _get_default_logger()
    except Exception:  # pragma: no cover - defensivo
        json_logger = _logger

    # Aplica o nível conforme o ambiente (debug só em dev).
    try:
        json_logger.setLevel(agno_log_level)
    except Exception:  # pragma: no cover - defensivo
        pass

    try:
        configure_agno_logging(custom_default_logger=json_logger)
    except Exception as exc:  # pragma: no cover - defensivo
        _logger.warning("Falha ao configurar o logging do Agno (%s).", exc)


def new_request_id() -> str:
    """Gera um `request_id` único por requisição.

    Retorna o hex de um UUID v4 (32 caracteres, sem hifens): identificador opaco,
    sem PII, adequado para correlacionar log e trace (Req 16.4). O mesmo valor
    deve ser gravado como campo no log estruturado e como atributo no span.

    Returns:
        Uma string hexadecimal única por requisição.
    """
    return uuid.uuid4().hex


def set_request_id_on_span(request_id: str) -> None:
    """Anexa o `request_id` ao span OTel atual como `app.request_id` (Req 16.4).

    É um **no-op** seguro quando o OpenTelemetry não está instalado ou quando não
    há span ativo/gravável — assim a correlação é *best-effort* e nunca
    interrompe a requisição (Req 16.2). Import preguiçoso e guardado.

    Args:
        request_id: O identificador da requisição (ver `new_request_id`). O mesmo
            valor gravado no log estruturado, para correlacionar log e trace.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        # OTel ausente: nada a fazer, seguimos sem anexar o atributo.
        return

    try:
        span = trace.get_current_span()
        # Sem span ativo, o OTel devolve um span "inválido"/no-op; `is_recording`
        # evita tentar anexar atributo onde não há gravação real.
        if span is not None and span.is_recording():
            span.set_attribute(_REQUEST_ID_SPAN_ATTRIBUTE, request_id)
    except Exception:  # pragma: no cover - defensivo
        # Correlacionar é best-effort: qualquer falha aqui é silenciada para não
        # afetar a requisição (Req 16.2).
        return
