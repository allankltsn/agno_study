"""Composition root — o **único** lugar que conhece as implementações concretas.

Este módulo é o ponto de montagem (a "raiz de composição") do agente. É aqui, e
somente aqui, que as classes concretas da infraestrutura (`OpenAIAdapter`,
`GroqAdapter`, `OllamaAdapter`, `FallbackLLM`, `StructuredLogger`) são conhecidas
e ligadas ao caso de uso (`AnswerQuestion`). O domínio e a aplicação continuam
dependendo apenas de abstrações (`LLMPort`) — nunca deste módulo (DIP).

Responsabilidades (do design):
- Ler a configuração tipada do ambiente via `load_settings()` (Req 4).
- Construir os adapters concretos na ordem de `provider_order`, via o helper
  `build_adapter(ProviderConfig)` (Composite + Strategy: cada adapter é uma
  `LLMPort` que entra na lista do `FallbackLLM`).
- Montar o `FallbackLLM([primary, secondary, ...])` com os parâmetros de SLA/
  resiliência de `Settings` e um `StructuredLogger`, e injetá-lo em
  `AnswerQuestion` (o caso de uso só vê uma `LLMPort`).
- Ligar a observabilidade uma única vez ao montar o agente, chamando
  `init_tracing(settings.otel_endpoint, debug_mode=settings.debug_mode)` (Req
  16.8). Toda a configuração de tracing/`configure_agno_logging`/exporter OTLP
  vive **apenas** aqui e na `infrastructure` — o domínio e a aplicação nunca a
  referenciam.
- Abortar cedo, com uma mensagem clara, quando a env var de chave exigida faltar
  ou a configuração for inválida — **nomeando** o valor de configuração ausente
  (o *nome* da env var), **sem nunca** expor o valor de nenhuma chave (Req 10.6,
  11.5).

Sobre segredos (Req 10.6, 11.5): este módulo lê apenas a *presença* da variável de
ambiente de chave (`os.environ.get(cfg.api_key_env)`); o **valor** nunca é
guardado, logado ou incluído em mensagens de erro. O SDK do provider lê a chave do
ambiente por conta própria no momento da chamada.

Sem imports de Agno no topo (Req 12.6): os adapters importam a Agno de forma
**tardia** (dentro dos próprios métodos/construtores). Assim este módulo permanece
importável sem o SDK instalado, e os tipos da Agno seguem confinados à
`infrastructure`.

Nota de estudo: `_DEFAULT_INSTRUCTIONS` e `_PRODUCT_CONTEXT` abaixo são
**placeholders de estudo**. Num agente real, a persona viria de um arquivo de
prompt versionado e a doc do produto de uma fonte de conteúdo (ainda sem RAG,
conforme o escopo). Aqui basta um documento mínimo para exercitar o arranjo.

Compatibilidade: `from __future__ import annotations` e tipos de `typing` para
Python 3.9+.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from suporte.application.use_cases.answer_question import AnswerQuestion
from suporte.domain.ports import LLMPort
from suporte.infrastructure.config import ProviderConfig, Settings, load_settings
from suporte.infrastructure.llm.fallback_llm import FallbackLLM
from suporte.infrastructure.observability.logging import StructuredLogger
from suporte.infrastructure.observability.tracing import init_tracing

# Nome da env var que aponta o endpoint do servidor Ollama (Req 15.5). O endereço
# nunca é embutido no código: é lido daqui e injetado no `OllamaAdapter`.
OLLAMA_HOST_ENV = "OLLAMA_HOST"


# --- Placeholders de estudo (persona + doc do produto) -----------------------
# Persona/regras de atendimento injetadas em cada adapter via `instructions`
# (RF-01, sem RAG). São propositalmente simples: o ponto de estudo é o *arranjo*
# (ports/adapters, fallback, DIP), não a engenharia de prompt.
_DEFAULT_INSTRUCTIONS: List[str] = [
    "Você é um assistente de suporte ao cliente, cordial e objetivo.",
    "Responda em português do Brasil, de forma clara e concisa.",
    "Use somente as informações da documentação do produto fornecida no contexto.",
]

# Documentação do produto injetada via `context` (RF-01, sem RAG). Documento
# mínimo, apenas para exercitar a montagem; substitua por conteúdo real em uso.
_PRODUCT_CONTEXT: Dict[str, Any] = {
    "produto": "Produto de Estudo",
    "faq": [
        {
            "pergunta": "Como redefino minha senha?",
            "resposta": (
                "Acesse Configurações > Segurança > Redefinir senha e siga as "
                "instruções enviadas para o seu e-mail cadastrado."
            ),
        },
        {
            "pergunta": "Qual o horário de atendimento?",
            "resposta": "O atendimento funciona de segunda a sexta, das 9h às 18h.",
        },
    ],
}


def _require_api_key(cfg: ProviderConfig) -> None:
    """Garante que a env var de chave exigida pelo provider está presente.

    Lê apenas a **presença** de `cfg.api_key_env` no ambiente. O **valor** da chave
    nunca é retornado, guardado, logado ou incluído em mensagens de erro (Req 10.6,
    11.5): o SDK do provider é quem lê a chave do ambiente na hora da chamada.

    Args:
        cfg: Configuração do provider (contém apenas o *nome* da env var da chave).

    Raises:
        RuntimeError: Se a env var nomeada por `cfg.api_key_env` estiver ausente ou
            vazia. A mensagem **nomeia a env var ausente** e o provider afetado, sem
            jamais expor qualquer valor de chave.
    """
    key_value = os.environ.get(cfg.api_key_env)
    if not key_value:
        # Aborta nomeando a env var ausente (e o provider), NUNCA o valor (Req 11.5).
        raise RuntimeError(
            "Configuração ausente: a variável de ambiente '{env}' é obrigatória "
            "para o provider '{provider}'. Defina '{env}' e tente novamente.".format(
                env=cfg.api_key_env, provider=cfg.name
            )
        )


def build_adapter(
    cfg: ProviderConfig,
    *,
    instructions: List[str] = _DEFAULT_INSTRUCTIONS,
    product_context: Dict[str, Any] = _PRODUCT_CONTEXT,
) -> LLMPort:
    """Constrói o adapter concreto (uma `LLMPort`) para um provider configurado.

    Mapeia `cfg.name` para a classe de adapter correspondente e injeta a persona
    (`instructions`) e a doc do produto (`product_context`). Antes de construir
    providers que exigem chave (``openai``/``groq``), verifica a **presença** da env
    var nomeada em `cfg.api_key_env` (Req 10.6, 11.5); ``ollama`` não exige chave e,
    em vez disso, tem seu endpoint lido de ``OLLAMA_HOST`` (Req 15.5).

    Os imports da Agno acontecem **dentro** dos adapters (tardios), então este
    módulo permanece importável sem o SDK e os tipos da Agno ficam confinados à
    `infrastructure` (Req 12.6).

    Args:
        cfg: Configuração do provider a construir (nome, model_id, api_key_env).
        instructions: Persona/regras injetadas no adapter (RF-01). Default: a
            persona de estudo deste módulo.
        product_context: Documentação do produto injetada como contexto (RF-01,
            sem RAG). Default: o documento de estudo deste módulo.

    Returns:
        Uma `LLMPort` pronta para entrar na lista do `FallbackLLM`.

    Raises:
        RuntimeError: Se a env var de chave exigida estiver ausente (nomeando-a,
            sem expor o valor), ou se `cfg.name` não corresponder a um provider
            suportado.
    """
    name = cfg.name.lower()

    if name == "openai":
        # Exige a chave presente ANTES de montar (o SDK a lerá do ambiente).
        _require_api_key(cfg)
        from suporte.infrastructure.llm.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(
            model_id=cfg.model_id,
            instructions=instructions,
            product_context=product_context,
        )

    if name == "groq":
        _require_api_key(cfg)
        from suporte.infrastructure.llm.groq_adapter import GroqAdapter

        return GroqAdapter(
            model_id=cfg.model_id,
            instructions=instructions,
            product_context=product_context,
        )

    if name == "ollama":
        # Ollama é um provider local: não exige chave de API. O endpoint vem de
        # OLLAMA_HOST (Req 15.5), nunca hardcoded; passamos o valor lido ao adapter.
        from suporte.infrastructure.llm.ollama_adapter import OllamaAdapter

        host = os.environ.get(OLLAMA_HOST_ENV)
        return OllamaAdapter(
            model_id=cfg.model_id,
            instructions=instructions,
            product_context=product_context,
            host=host,
        )

    # Nome não mapeado para um adapter conhecido: falha clara (defesa em
    # profundidade — `load_settings` já valida a ordem contra o registro).
    raise RuntimeError(
        "Provider não suportado no composition_root: '{}'. "
        "Suportados: 'openai', 'groq', 'ollama'.".format(cfg.name)
    )


def _build_fallback(settings: Settings) -> FallbackLLM:
    """Monta o `FallbackLLM` a partir de `Settings` (ordem + parâmetros de SLA).

    Constrói os adapters na ordem de `settings.provider_order` (o 1º é o primário,
    os demais são fallback) e os compõe no `FallbackLLM` com os timeouts, tentativas
    e backoff da configuração, além de um `StructuredLogger`.

    Args:
        settings: Configuração tipada e validada do agente.

    Returns:
        Um `FallbackLLM` (que também é uma `LLMPort`) pronto para injeção.

    Raises:
        RuntimeError: Propagada de `build_adapter` quando uma chave exigida falta
            ou um provider não é suportado (nomeando o valor de config ausente).
    """
    # Composite + Strategy: a ordem da lista É a ordem de preferência/fallback.
    adapters: List[LLMPort] = [
        build_adapter(settings.providers[name]) for name in settings.provider_order
    ]

    return FallbackLLM(
        adapters,
        per_attempt_timeout_s=settings.per_attempt_timeout_s,
        total_timeout_s=settings.total_timeout_s,
        max_attempts_per_provider=settings.max_attempts_per_provider,
        backoff_base_s=settings.backoff_base_s,
        logger=StructuredLogger(),
    )


def build_answer_question() -> AnswerQuestion:
    """Monta o caso de uso `AnswerQuestion` com o `FallbackLLM` já composto.

    Fluxo:
    1. `load_settings()` lê e valida a configuração do ambiente (Req 4). Erros de
       configuração (provider desconhecido, ordem vazia, timeouts inconsistentes)
       são propagados nomeando o problema — sem expor valores de chave.
    2. Liga a observabilidade uma única vez via
       `init_tracing(settings.otel_endpoint, debug_mode=settings.debug_mode)`
       (Req 16.8): configura o exporter OTLP para `OTEL_EXPORTER_OTLP_ENDPOINT`
       (ou desabilita o tracing, com aviso, quando ausente/vazio — Req 16.2) e
       liga `configure_agno_logging`. O `debug_mode` do Agno só é ativado em dev
       (Req 16.6/16.7). Toda essa configuração vive apenas na `infrastructure`/
       `composition_root` — nunca no domínio nem na aplicação.
    3. Constrói os adapters na ordem de `provider_order` (via `build_adapter`),
       verificando as chaves exigidas (Req 10.6, 11.5).
    4. Compõe o `FallbackLLM` com os parâmetros de SLA e o logger estruturado.
    5. Injeta o `FallbackLLM` em `AnswerQuestion` — o caso de uso só enxerga uma
       `LLMPort` (DIP): não sabe de fallback, timeout nem provider concreto.

    Returns:
        Um `AnswerQuestion` pronto para uso pela interface (CLI/API/AgentOS).

    Raises:
        ValueError: Erro de configuração vindo de `load_settings` (Req 4.5/4.6/6.4).
        RuntimeError: Env var de chave exigida ausente (nomeando-a, sem expor o
            valor) ou provider não suportado (Req 10.6, 11.5).
    """
    settings = load_settings()
    # Liga a observabilidade (OTel + configure_agno_logging) uma única vez, aqui
    # no ponto de montagem (Req 16.8). Endpoint ausente/vazio => tracing
    # desabilitado com aviso, sem interromper (Req 16.2). debug só em dev.
    init_tracing(settings.otel_endpoint, debug_mode=settings.debug_mode)
    fallback = _build_fallback(settings)
    # O caso de uso recebe a abstração; nunca conhece o concreto (DIP).
    return AnswerQuestion(llm=fallback)


def build_agent_for_agentos() -> Any:
    """Monta o `Agent` da Agno para o AgentOS "vestir" (Req 15.4).

    O AgentOS (porta 7777) precisa de um `Agent` da Agno, não do caso de uso. Para
    **não duplicar domínio**, reaproveitamos a mesma montagem: lemos a mesma
    configuração e usamos o **primeiro** provider de `provider_order` como o modelo
    do agente exposto, aplicando a mesma persona e doc do produto.

    A construção do `Agent` (e o import da Agno) é feita **aqui dentro** para manter
    a Agno confinada à infraestrutura/AgentOS e este módulo importável sem o SDK
    (Req 12.6). A verificação da chave exigida (Req 10.6, 11.5) é reutilizada via
    `_require_api_key` para providers que a exijam.

    Observabilidade (Req 16.8): como este também é um ponto de montagem do agente,
    liga a observabilidade via `init_tracing(settings.otel_endpoint,
    debug_mode=settings.debug_mode)` antes de construir o `Agent`, para que o
    AgentOS exporte tracing pelo mesmo endpoint (Req 16.1) e emita logs do Agno no
    formato JSON de uma linha (Req 16.3). Endpoint ausente/vazio desabilita o
    tracing sem interromper (Req 16.2); `debug_mode` só liga em dev (Req 16.6/16.7).

    Nota de escopo: o *wiring* completo do AgentOS é a tarefa 20.2; aqui entregamos
    uma montagem mínima e coerente (reusa `Settings` + persona + doc), suficiente
    para o AgentOS reaproveitar sem reescrever o domínio.

    Returns:
        Uma instância de ``agno.agent.Agent`` configurada com o provider primário,
        a persona de estudo e a doc do produto.

    Raises:
        ValueError: Erro de configuração vindo de `load_settings`.
        RuntimeError: Env var de chave exigida ausente (nomeando-a, sem expor o
            valor), ou provider primário não suportado pela montagem do AgentOS.
    """
    settings = load_settings()
    # Liga a observabilidade também neste ponto de montagem (Req 16.8), antes de
    # construir o Agent que o AgentOS irá vestir.
    init_tracing(settings.otel_endpoint, debug_mode=settings.debug_mode)
    # Provider primário = primeiro da ordem de preferência.
    primary_name = settings.provider_order[0]
    cfg = settings.providers[primary_name]
    name = cfg.name.lower()

    # Import tardio da Agno: confinado a este ponto de montagem do AgentOS.
    from agno.agent import Agent

    if name == "openai":
        _require_api_key(cfg)
        from agno.models.openai import OpenAIChat

        model = OpenAIChat(id=cfg.model_id)
    elif name == "groq":
        _require_api_key(cfg)
        from agno.models.groq import Groq

        model = Groq(id=cfg.model_id)
    elif name == "ollama":
        from agno.models.ollama import Ollama

        model = Ollama(id=cfg.model_id, host=os.environ.get(OLLAMA_HOST_ENV))
    else:
        raise RuntimeError(
            "Provider primário não suportado para o AgentOS: '{}'.".format(cfg.name)
        )

    return Agent(
        model=model,
        instructions=_DEFAULT_INSTRUCTIONS,
        # Agno v2: doc do produto injetada via `dependencies` (+
        # add_dependencies_to_context=True), no lugar do antigo `context=`.
        dependencies=_PRODUCT_CONTEXT,
        add_dependencies_to_context=True,
    )
