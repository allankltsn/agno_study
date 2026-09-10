# Implementation Plan: Agente 01 — Suporte com SLA e Fallback de Provider

## Overview

Plano de implementação incremental e orientado a testes para construir o agente em
`agents/01-suporte-sla-fallback/`. A ordem é **inside-out**: primeiro o domínio (Python
puro, testável sem rede), depois aplicação, configuração, observabilidade, adapters, o
núcleo de resiliência (`FallbackLLM`), composição, CLI, verificações estáticas de
arquitetura/segurança e, por fim, smoke test e documentação.

Cada tarefa referencia os critérios de requisito que satisfaz (formato `Requirements X.Y`)
e constrói sobre as anteriores, terminando com o *wiring* completo — sem código órfão.

Linguagem: **Python 3.9+ / Agno v2** (definida explicitamente no design e no escopo §6).
Testes de propriedade usam `hypothesis`; testes de unidade usam `pytest`. A Agno é
importada **somente** na camada `infrastructure`.

A estratégia de testes segue a skill `agno-testing-strategy` (Requisito 17): três camadas
de e2e (transporte determinístico como gate de PR; comportamento com LLM real/Ollama em
nightly, marcado `e2e`; sistema real em pré-release, marcado `system`), separando os três
sentidos de "eficiência" — operacional, eficácia e resiliência. As tarefas 22–25 adicionam
os fakes reutilizáveis, o eval de eficácia (golden set), a eficiência operacional e o smoke
de sistema.

Convenções:
- Sub-tarefas marcadas com `*` são de teste e **opcionais** (podem ser puladas num MVP).
- Tarefas sem `*` são obrigatórias e devem ser implementadas.
- Providers fake (`FakeLLM`, `ProgrammableAdapter`/`AlwaysOk`/`AlwaysFails`/`SlowProvider`,
  `FakeClock`) vivem em `tests/fakes/` e **nunca** chamam rede (Req 17.2).

## Tasks

- [x] 1. Scaffolding do projeto isolado (uv) e esqueleto de pacotes
  - [x] 1.1 Criar estrutura de diretórios e arquivos de projeto com `uv`
    - Criar `agents/01-suporte-sla-fallback/` com `pyproject.toml` (nome do projeto,
      `requires-python = ">=3.9"`, dependência `agno` **v2 fixada** e `python-dotenv`;
      dev-deps `pytest` e `hypothesis`; configurar `[tool.pytest.ini_options]` com
      `pythonpath = ["src"]`)
    - Criar `.gitignore` incluindo `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`
    - Criar `.env.example` **somente com placeholders/valores vazios** (nunca chaves reais):
      `OPENAI_API_KEY=`, `GROQ_API_KEY=`, `MODEL_PROVIDER_ORDER=openai,groq`,
      `LLM_PER_ATTEMPT_TIMEOUT=10`, `LLM_TOTAL_TIMEOUT=25`, `LLM_MAX_ATTEMPTS=2`,
      `LLM_BACKOFF_BASE=0.5`
    - Criar o esqueleto do pacote `src/suporte/` com `__init__.py` em cada pacote:
      `domain/`, `application/`, `application/use_cases/`, `infrastructure/`,
      `infrastructure/observability/`, `infrastructure/llm/`, `interface/`,
      `interface/cli/`; e a árvore `tests/domain/`, `tests/application/`,
      `tests/infrastructure/`
    - Não conter nenhum import que referencie outros agentes fora deste diretório
    - _Requirements: 12.1, 12.2, 12.4, 10.3, 10.4_

  - [x] 1.2 Verificar resolução de dependências com uv
    - Documentar/rodar `uv sync` (ou `uv lock`) para garantir que a resolução conclui sem
      erros e versionar `uv.lock`
    - _Requirements: 12.3_

- [x] 2. Camada de domínio (Python puro, sem framework)
  - [x] 2.1 Implementar `domain/ports.py` (`Answer` e `LLMPort`)
    - `Answer`: `@dataclass(frozen=True)` com `text`, `provider`, `prompt_tokens`,
      `completion_tokens`, `latency_ms`, `out_of_scope=False`; propriedade derivada
      `total_tokens = prompt_tokens + completion_tokens`
    - `LLMPort`: `Protocol` `@runtime_checkable` com `ask(prompt) -> Answer` e
      `ask_stream(prompt) -> Iterator[str]`
    - Docstrings de módulo/classe/método explicando o contrato (material de estudo)
    - _Requirements: 1.2, 8.5, 12.6_

  - [x] 2.2 Implementar `domain/exceptions.py`
    - `DomainError(Exception)` base; `ProviderUnavailable(DomainError)` com atributo
      `attempts: list[str]` (providers tentados na ordem) e mensagem padrão;
      `OutOfScopeQuestion(DomainError)`
    - Docstrings explicando quando cada erro nasce
    - _Requirements: 5.5, 3.1_

  - [x] 2.3 Escrever testes de unidade do domínio
    - Testar `Answer.total_tokens` (soma correta), imutabilidade (`frozen`) e default de
      `out_of_scope`; testar que `ProviderUnavailable.attempts` preserva a ordem
    - _Requirements: 1.2, 8.5, 5.5_

- [x] 3. DTOs da camada de aplicação
  - [x] 3.1 Implementar `application/dto.py`
    - `Mode(str, Enum)` com `RUN = "run"` e `STREAM = "stream"`
    - `AnswerRequest` (`@dataclass(frozen=True)`): `question: str`, `mode: Mode = Mode.RUN`
    - `AnswerResponse` (`@dataclass(frozen=True)`): `text`, `provider`, `total_tokens`,
      `latency_ms`, `used_fallback`, `out_of_scope`, `escalated`
    - _Requirements: 2.1, 11.1_

- [ ] 4. Caso de uso `AnswerQuestion` (testes primeiro, com FakeLLM sem rede)
  - [-] 4.1 Escrever `FakeLLM` de teste e testes de unidade do caso de uso
    - Criar em `tests/application/` um `FakeLLM` que implementa `LLMPort` sem rede
      (retorna `Answer` fixo, ou `Answer` com `out_of_scope=True`, ou levanta
      `ProviderUnavailable`)
    - Cobrir: pergunta vazia/apenas espaços → resposta de validação, `escalated=false`,
      **sem** chamar a `LLMPort`; `out_of_scope=True` → mensagem de encaminhamento com
      `out_of_scope=true` e `escalated=true`; `ProviderUnavailable` → degradação graciosa
      com `escalated=true` e sem stacktrace; modo inválido → rejeição sem invocar a port
    - _Requirements: 1.3, 2.4, 3.2, 3.3, 3.4, 7.5_

  - [x] 4.2 Implementar `application/use_cases/answer_question.py`
    - `AnswerQuestion.__init__(self, llm: LLMPort)` (DIP: recebe abstração injetada; **não
      conhece fallback**)
    - `execute(request: AnswerRequest) -> AnswerResponse`: validar pergunta não vazia;
      chamar `llm.ask`; mapear `out_of_scope` → encaminhamento; capturar
      `ProviderUnavailable` → degradação graciosa (mensagem amigável, sem stacktrace);
      calcular `used_fallback` a partir do provider retornado
    - `execute_stream(question: str) -> Iterator[str]`: chamar `llm.ask_stream`
      preservando a ordem dos fragmentos
    - Rejeitar modo não suportado sem invocar `ask`/`ask_stream`
    - _Requirements: 1.1, 1.3, 2.1, 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 7.5_

- [x] 5. Configuração tipada por ambiente
  - [x] 5.1 Implementar `infrastructure/config.py`
    - `ProviderConfig(frozen)`: `name`, `model_id`, `api_key_env` (**nome** da env var,
      nunca o valor)
    - `Settings(frozen)`: `provider_order: list[str]`, `providers: dict[str, ProviderConfig]`,
      `per_attempt_timeout_s=10.0`, `total_timeout_s=25.0`, `max_attempts_per_provider=2`,
      `backoff_base_s=0.5`
    - `load_settings()`: usar `python-dotenv` + `os.getenv`; parsear `MODEL_PROVIDER_ORDER`
      como lista separada por vírgula (com ordem padrão contendo ≥ 1 provider); aplicar
      defaults; validar: `ValueError` nomeando provider desconhecido, `ValueError` se
      ordem resolvida vazia, `ValueError` se `total_timeout_s < per_attempt_timeout_s`
    - _Requirements: 4.1, 4.5, 4.6, 6.1, 6.3, 6.4, 7.1, 10.2_

  - [x] 5.2 Escrever testes de unidade da configuração
    - Testar parsing de `MODEL_PROVIDER_ORDER`, defaults aplicados, e cada ramo de
      `ValueError` (provider desconhecido, ordem vazia, `total < per_attempt`); testar que
      `ProviderConfig` guarda apenas o nome da env var
    - _Requirements: 4.1, 4.5, 4.6, 6.4, 10.2_

- [x] 6. Log estruturado (observabilidade)
  - [x] 6.1 Implementar `infrastructure/observability/logging.py`
    - `log_request(*, question_hash, provider, latency_ms, prompt_tokens,
      completion_tokens, used_fallback, attempts, result)` emitindo **um** JSON estruturado
      por requisição
    - Incluir `total_tokens` derivado; `result` ∈ {`"ok"`, `"error"`}; nunca logar chaves
      de API; a pergunta aparece **somente** como `question_hash` determinístico (helper de
      hash da pergunta)
    - _Requirements: 8.3, 8.4, 9.1, 9.2, 9.3, 9.4, 9.5, 10.5_

  - [x] 6.2 Escrever testes de unidade do logger
    - Capturar a saída e asserir: exatamente um registro; presença de `question_hash`,
      `provider`, `latency_ms`, tokens, `used_fallback`, `attempts`, `result`; tipos
      corretos (`latency_ms>=0`, tokens `>=0`, `attempts` lista, `used_fallback` bool);
      texto em claro da pergunta **não** aparece; nenhuma chave de API é logada
    - _Requirements: 9.1, 9.2, 9.5, 10.5_

- [x] 7. Adapters de provider (envolvem a Agno — único lugar com tipos Agno)
  - [x] 7.1 Implementar `infrastructure/llm/openai_adapter.py`
    - `OpenAIAdapter` implementa `LLMPort`; importa `agno` **dentro** do módulo/`__init__`;
      monta `Agent` com `OpenAIChat(id=model_id)`, injetando doc do produto via
      `instructions`/`context` (sem RAG)
    - `ask` converte a resposta da Agno em `Answer` (extrai `prompt_tokens`/
      `completion_tokens` ≥ 0, com zero quando omitidos; `provider`; `latency_ms`); marca
      `out_of_scope=True` quando o modelo sinaliza falta de fundamentação; traduz falhas de
      rede/timeout/auth em `ProviderUnavailable`
    - `ask_stream` gera fragmentos de texto na ordem de emissão
    - _Requirements: 1.1, 1.2, 2.3, 3.1, 8.1, 8.2, 12.6_

  - [x] 7.2 Implementar `infrastructure/llm/groq_adapter.py`
    - `GroqAdapter` análogo ao OpenAI usando `agno.models.groq.Groq`; mesmo contrato
      (`LLMPort`), mesma conversão para `Answer`, mesma tradução de erros para
      `ProviderUnavailable` (LSP)
    - _Requirements: 1.1, 1.2, 2.3, 3.1, 8.1, 8.2, 12.6_

  - [x] 7.3 Escrever testes de unidade dos adapters com cliente Agno fake
    - Injetar um duplo de teste no lugar do `Agent`/modelo Agno (sem rede) e verificar a
      conversão para `Answer` (tokens/provider/latência), marcação de `out_of_scope`, e
      tradução de erro de rede/auth em `ProviderUnavailable`
    - _Requirements: 1.2, 3.1, 8.1, 8.2_

- [ ] 8. `FallbackLLM` — núcleo de resiliência (Composite + Strategy)
  - [x] 8.1 Implementar `infrastructure/llm/fallback_llm.py`
    - `FallbackLLM` implementa `LLMPort`; recebe `providers: Sequence[LLMPort]`,
      `per_attempt_timeout_s`, `total_timeout_s`, `max_attempts_per_provider`,
      `backoff_base_s`, `logger`, e `clock=time.monotonic` (injetável para teste)
    - `call_with_timeout` via `concurrent.futures.ThreadPoolExecutor` +
      `future.result(timeout=deadline)`; deadline = `min(per_attempt, restante_total)`
    - Iterar providers na ordem (Strategy); por provider, retry até
      `max_attempts_per_provider` com **backoff exponencial** `backoff_base*2^(n-1)`;
      marcar `used_fallback=true` a partir do 2º provider; verificar SLA total a cada
      iteração e interromper novas tentativas ao atingir `total_timeout_s`
    - Em sucesso: retornar `Answer` com `provider`/`latency_ms` e logar `result="ok"`; ao
      esgotar um provider: logar evento de fallback; ao esgotar todos: levantar
      `ProviderUnavailable(attempts)` e logar `result="error"`
    - **Comentários explicativos na lógica de fallback** (exigência de doc do charter)
    - `ask_stream` análogo, respeitando timeout/fallback
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.2, 6.5, 7.2, 7.3, 7.4, 13.4_

  - [-] 8.2 Property test — Property 1: Ordem de fallback
    - **Property 1: Ordem de fallback** — se `p1` responde no SLA, `ask` retorna `p1` e
      `used_fallback=false`. Usar `hypothesis` + provider fake `AlwaysOk` como primário
    - **Validates: Requirements 5.1**

  - [-] 8.3 Property test — Property 2: Fallback automático
    - **Property 2: Fallback automático** — `p1` falha em todas as `max_attempts` e `p2`
      responde → `ask` retorna `p2`, `provider=p2`, e um evento de fallback é logado. Usar
      `AlwaysFails` (primário) + `AlwaysOk` (secundário)
    - **Validates: Requirements 5.2, 5.3, 5.4**

  - [-] 8.4 Property test — Property 4: SLA total nunca excedido
    - **Property 4: SLA total** — com `clock` injetado e `SlowProvider`/`AlwaysFails`, o
      tempo total até retornar ou levantar `ProviderUnavailable` ≤ `total_timeout_s` +
      margem de uma verificação
    - **Validates: Requirements 6.5**

  - [-] 8.5 Property test — Property 7: Consistência run/stream
    - **Property 7: Consistência run/stream** — para um provider fake gerando texto por
      chunks, `"".join(ask_stream(prompt))` é consistente em conteúdo com `ask(prompt).text`
    - **Validates: Requirements 2.5**

  - [x] 8.6 Testes de unidade do backoff e exaustão total
    - Verificar: retry aguarda intervalos de backoff exponencial (com clock/sleep fake);
      exaustão do último provider levanta `ProviderUnavailable(attempts)` com a ordem
      correta; log de `result="error"` quando todos falham
    - _Requirements: 5.5, 5.6, 7.2, 7.3, 7.4_

- [x] 9. Composition root (único lugar que conhece implementações concretas)
  - [x] 9.1 Implementar `composition_root.py`
    - `build_answer_question() -> AnswerQuestion`: `load_settings()`, construir adapters na
      ordem de `provider_order` (via helper `build_adapter(ProviderConfig)`), montar
      `FallbackLLM([primary, secondary, ...])` com os `Settings` e o logger, e injetar em
      `AnswerQuestion`
    - Se uma env var de chave exigida faltar / erro de configuração: abortar nomeando o
      valor de configuração ausente, sem expor valores de chave
    - _Requirements: 4.2, 10.6, 11.4, 11.5_

  - [x] 9.2 Testes do composition root
    - Com env controlado, verificar montagem na ordem correta e que trocar
      `MODEL_PROVIDER_ORDER` altera a ordem sem tocar `domain/`/`application/`; verificar
      abort nomeando a env var ausente
    - _Requirements: 4.2, 10.6, 11.5_

- [x] 10. Interface CLI
  - [x] 10.1 Implementar `interface/cli/main.py`
    - Ler a pergunta (validar 1..4000 caracteres) e o modo (`run`/`stream`); rejeitar
      pergunta vazia/ausente/>4000 com mensagem de validação **sem** chamar o caso de uso;
      rejeitar modo inválido indicando os modos suportados
    - Chamar o caso de uso via `composition_root`; formatar a saída incluindo
      `provider`, `total_tokens`, `latency_ms`, `used_fallback`; traduzir erros de domínio
      em mensagens amigáveis sem stacktrace
    - _Requirements: 2.1, 11.1, 11.2, 11.3, 11.6_

  - [x] 10.2 Testes da CLI
    - Testar validação de tamanho (vazio, >4000), modo inválido, e formatação de saída
      (com metadados) usando um caso de uso fake/injetado
    - _Requirements: 11.1, 11.2, 11.3, 11.6_

- [x] 11. Checkpoint — garantir que todos os testes passam
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Verificações estáticas de arquitetura e segurança
  - [x] 12.1 Teste de isolamento de camadas — Property 6
    - **Property 6: Isolamento do domínio** — varrer `domain/` e `application/` e falhar se
      importarem `agno`, `openai` ou `groq`, nomeando o módulo infrator
    - **Validates: Requirements 12.5, 12.6**

  - [x] 12.2 Teste de segredos fora do código — Property 8
    - **Property 8: Segredos fora do código** — asserir ausência de literais de chave de
      API no código-fonte; presença de `.env.example`; `.env` listado no `.gitignore`
    - **Validates: Requirements 10.1, 10.3, 10.4**

- [x] 13. Smoke test e roteiro de aceite (mapeando escopo §9)
  - [x] 13.1 Escrever script de smoke test e checklist de aceite
    - Script `run`/`stream` para uma pergunta conhecida via `composition_root`; caso de
      pergunta fora de escopo (encaminhamento, não alucinação); forçar falha do primário
      (chave inválida/timeout) e observar log de fallback; inspecionar log estruturado
      (provider/latência/tokens). Documentar o checklist mapeado ao escopo §9
    - _Requirements: 5.1, 5.2, 9.1_

  - [x] 13.2 Testes de integração/live com providers reais (OPCIONAL — requer chaves)
    - Testes marcados (ex.: `@pytest.mark.integration`) que chamam providers reais;
      **não** rodam no CI padrão (exigem `OPENAI_API_KEY`/`GROQ_API_KEY` reais)
    - _Requirements: 1.1, 2.1, 2.3_

- [x] 14. Documentação obrigatória do agente (charter §6)
  - [x] 14.1 Escrever `README.md`
    - Quatro seções obrigatórias: o que o agente estuda; como rodar com `uv`; o que
      observar (provider/latência/tokens/fallback nos logs); pré-requisitos e limitações
    - _Requirements: 13.1_

  - [x] 14.2 Escrever `DESENVOLVIMENTO.md`
    - Conceito estudado; decisões de arquitetura por camada; por que cada port/adapter
      existe; **por que o fallback vive na infraestrutura e não no caso de uso**;
      Composite+Strategy, OCP, DIP; trade-offs e alternativas; fatores de produção
      aplicados; como testar e o que observar; o que faria diferente em produção real
    - _Requirements: 13.2_

  - [x] 14.3 Documentar API FastAPI, deploy e ambiente local no `README.md`/`DESENVOLVIMENTO.md`
    - Acrescentar ao `README.md` e ao `DESENVOLVIMENTO.md`: como rodar a app FastAPI
      localmente (`uvicorn`) e os caminhos de deploy (Mangum padrão vs container + AWS
      Lambda Web Adapter); como subir o `docker-compose` e o AgentOS (porta 7777); como a
      observabilidade (OTel/tracing + `configure_agno_logging` + `request_id`) funciona e é
      trocada por env sem mudar código
    - _Requirements: 13.1, 13.2, 14.4, 14.5, 15.1, 15.4, 16.1_

- [x] 15. Checkpoint — garantir que todos os testes do núcleo (CLI) passam
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. `OllamaAdapter` — provider de fallback local (docker-compose)
  - [x] 16.1 Implementar `infrastructure/llm/ollama_adapter.py`
    - `OllamaAdapter` implementa `LLMPort`; importa `agno` **dentro** do módulo; monta
      `Agent` com `agno.models.ollama.Ollama(id=model_id, host=host)`, com `host` vindo de
      env (`OLLAMA_HOST`), nunca hardcoded; injeta doc do produto via `instructions`/
      `context` (sem RAG)
    - `ask` converte a resposta da Agno em `Answer` (extrai `prompt_tokens`/
      `completion_tokens` ≥ 0 com zero quando omitidos; `provider="ollama"`; `latency_ms`);
      marca `out_of_scope=True` quando o modelo sinaliza falta de fundamentação; traduz
      falhas de rede/timeout em `ProviderUnavailable` (LSP: mesmo contrato de erro)
    - `ask_stream` gera fragmentos na ordem de emissão; é só mais uma `LLMPort` na lista do
      `FallbackLLM` (mesmo Composite + Strategy — acrescentar `ollama` à `provider_order`
      não toca domínio nem caso de uso)
    - Agno importada **somente** aqui (tipos Agno confinados à `infrastructure`)
    - _Requirements: 15.1, 15.5, 12.6_

  - [x] 16.2 Escrever teste de unidade do `OllamaAdapter` com cliente Agno fake
    - Injetar um duplo de teste no lugar do `Agent`/modelo Ollama (**sem rede**) e verificar
      a conversão para `Answer` (tokens/provider/latência) e a tradução de erro de
      rede/timeout em `ProviderUnavailable`
    - _Requirements: 15.1_

- [x] 17. Observabilidade — tracing OpenTelemetry, `configure_agno_logging` e `request_id`
  - [x] 17.1 Implementar `infrastructure/observability/tracing.py`
    - `init_tracing(otel_endpoint, *, debug_mode=False)`: configurar o exporter OTLP a
      partir de `OTEL_EXPORTER_OTLP_ENDPOINT`; se o endpoint estiver ausente/vazio,
      **desabilitar** o tracing, registrar aviso no log estruturado e **continuar** a
      requisição sem interromper
    - Chamar `configure_agno_logging(custom_default_logger=json_logger, ...)` para o Agno
      emitir **JSON de uma linha** idêntico em estrutura ao `StructuredLogger`
    - `new_request_id()` (id único por requisição) e `set_request_id_on_span(request_id)`
      (atributo `app.request_id` no span OTel atual)
    - `debug_mode` do Agno só em dev; **desligado por padrão** fora de dev
    - _Requirements: 16.1, 16.2, 16.3, 16.6, 16.7_

  - [x] 17.2 Ligar a geração de `request_id` ao fluxo da requisição
    - Gerar o `request_id` no início da requisição e propagá-lo pelo caso de uso/adapters/
      logger, de modo que o **mesmo** valor seja gravado no log estruturado (campo) e no
      span OTel (atributo `app.request_id`)
    - Garantir que a pergunta apareça **somente** como `question_hash` e que nenhuma PII,
      texto de pergunta em claro ou chave de API vaze em log ou trace
    - _Requirements: 16.4, 16.5_

  - [x] 17.3 Ligar `init_tracing` no `composition_root`
    - `composition_root` chama `init_tracing(settings.otel_endpoint, debug_mode=...)` ao
      montar o agente; toda a configuração de observabilidade (tracing,
      `configure_agno_logging`, exporter OTLP) reside **apenas** em
      `infrastructure`/`composition_root`, mantendo `domain`/`application` limpos
    - _Requirements: 16.8_

  - [x] 17.4 Property test — Property 13: correlação `request_id` e ausência de PII
    - **Property 13: Correlação request_id e ausência de PII** — com um exporter OTel
      **em memória/fake** e um log em memória, asserir que o **mesmo** `request_id` aparece
      no campo do log e no atributo `app.request_id` do span; asserir que nenhuma PII, texto
      de pergunta em claro ou chave de API aparece (pergunta só como `question_hash`)
    - **Validates: Requirements 16.3, 16.4, 16.5**

  - [x] 17.5 Teste — endpoint OTLP ausente/vazio desabilita tracing sem interromper
    - Com `OTEL_EXPORTER_OTLP_ENDPOINT` ausente/vazio, verificar que `init_tracing`
      desabilita a exportação, registra um aviso e a requisição prossegue normalmente
    - _Requirements: 16.2_

  - [x] 17.6 Teste estático — Property 12: observabilidade só na infraestrutura
    - **Property 12: Endpoints por env; observabilidade só na infraestrutura** — varrer
      `domain/` e `application/` e falhar se referenciarem tracing,
      `configure_agno_logging` ou o exporter OTLP
    - **Validates: Requirements 15.5, 16.1, 16.8**

- [x] 18. App FastAPI (`interface/api`) reutilizando o caso de uso
  - [x] 18.1 Implementar `interface/api/api.py`
    - App **FastAPI** com Pydantic `ChatIn` (`question` 1..4000, `mode` no enum `Mode`) e
      `ChatOut`; rotas `POST /chat`, `POST /chat/stream` e `GET /health`
    - Reutilizar `AnswerQuestion` via `composition_root` (montado **uma vez** por
      processo/container, fora das rotas); traduzir `DomainError` em HTTP `502` sem
      stacktrace nem detalhes internos; expor docs OpenAPI (`/docs`)
    - **Não** importar tipos da Agno (tipos Agno confinados à `infrastructure`)
    - _Requirements: 14.1, 14.2, 14.3, 14.6, 14.7, 14.8, 14.9_

  - [x] 18.2 Implementar `interface/api/main.py`
    - `handler = Mangum(app)` (caminho de deploy **padrão**), importando a mesma `app` de
      `interface/api/api.py`
    - _Requirements: 14.5_

  - [x] 18.3 Testes da app FastAPI com `TestClient` (sem rede, `LLMPort` fake)
    - **Property 11: Mesma app FastAPI nos dois deploys, com validação na borda** —
      overriding do caso de uso com uma `LLMPort` fake; validar que `question` de 0 e 4001
      caracteres e `mode` inválido → `422` **sem** invocar o caso de uso; entrada válida →
      `ChatOut`; `GET /health` → `200`; `DomainError` → `502` sem stacktrace; `/docs`
      acessível
    - **Validates: Requirements 14.2, 14.3, 14.7, 14.8**
    - _Requirements: 14.9_

  - [x] 18.4 Teste estático — Property 10: `interface/api` não importa Agno
    - **Property 10: FastAPI reutiliza o domínio, sem tipos Agno, erro → 502** — varrer
      `interface/api/` e falhar se importar `agno`, nomeando o módulo infrator
    - **Validates: Requirements 14.1, 14.6, 14.7**

- [x] 19. Empacotamento serverless (SAM Mangum padrão + alternativa container)
  - [x] 19.1 Criar `template.yaml` (SAM)
    - API Gateway + Lambda (`Handler: main.handler`) + Lambda Layer com as deps pesadas
      (`agno`, `fastapi`, `mangum`, SDKs de provider); `Timeout` <= 29s; variáveis de
      ambiente para ordem/timeouts de provider; `Tracing: Active` (X-Ray) e nota da ADOT
      layer para encaminhar OTel → X-Ray
    - Arquivo de infraestrutura; **não** fazer deploy ao vivo — apenas criar o arquivo
    - _Requirements: 14.3, 14.4, 16.1_

  - [x] 19.2 Criar `Dockerfile` (caminho alternativo de container)
    - Imagem de container com **AWS Lambda Web Adapter** + `uvicorn` rodando a **mesma** app
      FastAPI, para quando o pacote exceder o limite de Lambda Layers (250 MB)
    - Arquivo de infraestrutura; **não** fazer deploy ao vivo — apenas criar o arquivo
    - _Requirements: 14.5_

- [x] 20. Ambiente de desenvolvimento local (docker-compose + AgentOS + LocalStack)
  - [x] 20.1 Criar `docker-compose.yml`
    - Subir LocalStack (`apigateway`, `lambda`, `dynamodb`, `secretsmanager`, `ssm`),
      Ollama, AgentOS (porta 7777) e observabilidade via **perfis do Compose**: SigNoz
      (padrão) OU Grafana+Tempo+Loki+Prometheus (perfil `grafana`), com Langfuse opcional
      (perfil `langfuse`)
    - _Requirements: 15.1, 15.2, 15.3_

  - [x] 20.2 Criar `agentos/app.py`
    - AgentOS que "veste" `build_agent_for_agentos()` (reusa o `composition_root`, sem
      duplicar domínio), na porta 7777
    - _Requirements: 15.4_

  - [x] 20.3 Criar `.env.local.example` (versionado) e ignorar `.env.local`
    - Placeholders sem valores reais: `MODEL_PROVIDER_ORDER=openai,ollama`, `OLLAMA_HOST`,
      `AWS_ENDPOINT_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, etc.; adicionar `.env.local` ao
      `.gitignore`
    - _Requirements: 15.5, 15.6_

  - [x] 20.4 Criar `localstack/init/01-bootstrap.sh`
    - Criar tabela DynamoDB + segredo via `awslocal`, de forma **idempotente** ao ser
      reexecutado com os recursos já existentes
    - _Requirements: 15.7, 15.8_

  - [x] 20.5 Criar configs de suporte da observabilidade
    - `otel/collector-config.yaml` (perfil `grafana`) e `grafana/datasources.yaml`
      (datasources Tempo/Loki/Prometheus provisionados)
    - _Requirements: 15.2_

  - [x] 20.6 Smoke test local (OPCIONAL — requer serviços locais)
    - Rodar a CLI contra os serviços locais (fallback via Ollama) e visualizar os traces no
      backend de observabilidade escolhido
    - _Requirements: 15.1, 15.4_

- [x] 22. Fakes reutilizáveis e marcadores de teste (base da estratégia de testes)
  - [x] 22.1 Consolidar fakes reutilizáveis em `tests/fakes/`
    - Criar `tests/fakes/llm.py` com `FakeLLM` (retorna `Answer` fixo ou `out_of_scope=True`),
      `ProgrammableAdapter` (falha um número configurável de vezes antes de responder, ou
      falha sempre; conta chamadas; aceita `delay_s`) e `FakeClock` (relógio controlável com
      `advance(seconds)`, compatível com `clock=time.monotonic`); nenhum acesso à rede
    - Consolidar aqui os fakes já usados nas tarefas 4.1 e 8.x (`AlwaysOk`, `AlwaysFails`,
      `SlowProvider`) para reuso entre camadas
    - _Requirements: 17.2, 17.3_

  - [x] 22.2 Configurar marcadores `pytest`
    - Em `pyproject.toml`, declarar `[tool.pytest.ini_options] markers` com `e2e`
      (comportamento com LLM real/Ollama), `system` (deploy real) e `integration`
      (providers reais); documentar o comando do gate de PR
      (`pytest -m "not e2e and not system and not integration"`)
    - _Requirements: 17.1, 17.11_

- [x] 23. Eval de eficácia — golden set e scorers (camada 2)
  - [x] 23.1 Criar o golden set versionado
    - Criar `tests/eval/dataset.jsonl` com ≥ 20 casos: `in_scope` (com âncoras
      `must_mention`) e `out_of_scope` (que devem escalar), cobrindo in-scope fácil,
      in-scope difícil e fora de escopo
    - _Requirements: 17.4_

  - [x] 23.2 Implementar os scorers
    - Criar `tests/eval/scorers.py` com `score_deterministic` (verifica `escalated=True` nos
      casos `out_of_scope` e presença das âncoras `must_mention` nos `in_scope`) e,
      opcionalmente, `score_grounded_llm_judge` (sinal agregado por taxa, nunca gate por caso)
    - _Requirements: 17.5, 17.7_

  - [x] 23.3 Harness de eval de eficácia (marcado `e2e`)
    - Criar `tests/eval/test_agent_quality.py`: carregar o dataset, executar via
      `build_answer_question`, aplicar os scorers e agregar por **taxa** —
      `test_acuracia_geral` (≥ threshold) e `test_recall_out_of_scope` (≥ threshold **mais
      alto** que a acurácia geral); marcar `@pytest.mark.e2e`; rodar com Ollama por padrão
      (via `MODEL_PROVIDER_ORDER`), provider real só em pré-release; `temperature` baixa e
      thresholds com folga; registrar o resultado por execução para detectar regressão
    - _Requirements: 17.5, 17.6, 17.12, 17.14_

- [x] 24. Eficiência operacional — orçamentos sobre o log estruturado
  - [x] 24.1 Testes de eficiência operacional
    - Criar `tests/eval/test_operational_efficiency.py`: executar um lote (golden set ou
      fakes com `delay_s` conhecido), capturar os logs estruturados, e asserir orçamentos —
      p95 de `latency_ms` ≤ budget, média de tokens ≤ budget, taxa de `used_fallback` ≤
      budget, e nenhuma `latency_ms` acima de `total_timeout_s`; latência sempre por
      percentil, nunca média nem máximo
    - _Requirements: 17.8, 17.9, 17.10_

- [x] 25. Smoke de sistema real (camada 3)
  - [x] 25.1 Smoke test contra o deploy (marcado `system`)
    - Criar `tests/system/test_smoke.py` (marcado `@pytest.mark.system`): contra
      `AGENT_BASE_URL` (LocalStack ou AWS), verificar `GET /health` → `200` e `POST /chat`
      → `200` ou `502`, sem depender de resposta determinística do modelo; testar também o
      `handler` Mangum com um evento API Gateway sintético (sem AWS); reusar o bootstrap
      idempotente do LocalStack no setup
    - _Requirements: 17.13_

- [x] 26. Checkpoint final — garantir que todos os testes passam
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tarefas marcadas com `*` são opcionais (testes) e podem ser puladas para um MVP mais
  rápido; a tarefa 13.2 (integração live), a 20.6 (smoke test local), a 23.3 (eval de
  eficácia), a 24.1 (eficiência operacional) e a 25.1 (smoke de sistema) exigem chaves
  reais, serviços locais ou LLM real e ficam fora do gate de PR (marcadas `e2e`/`system`/
  `integration`).
- Cada tarefa referencia requisitos específicos para rastreabilidade.
- Os checkpoints (11, 15 e 26) garantem validação incremental; o checkpoint final (26)
  é a última tarefa.
- **Estratégia de testes (Req 17)**: as tarefas 22–25 implementam as três camadas de e2e.
  O gate de PR roda só a camada determinística
  (`pytest -m "not e2e and not system and not integration"`): domínio/aplicação, resiliência
  do `FallbackLLM` (com `FakeClock`), rotas FastAPI (`TestClient`), observabilidade,
  verificações estáticas e eficiência operacional com fakes. O eval de eficácia (camada 2,
  Ollama) roda em nightly e o smoke de sistema (camada 3, LocalStack/AWS) em pré-release.
- Testes de propriedade (`hypothesis`) validam as propriedades universais do design
  (P1, P2, P4, P6, P7, P8, P10, P11, P12, P13, P14); testes de unidade validam exemplos e
  casos de borda. P14 (eficácia/eficiência por taxa e orçamento) é verificada pelas tarefas
  23–24.
- A Agno é importada apenas em `infrastructure/llm/*_adapter.py` e em `agentos/app.py`; o
  domínio, a aplicação e a `interface/api` (FastAPI) permanecem sem tipos da Agno
  (verificado pelas Properties 6, 10 e 12).
- As capacidades adicionadas (entrega serverless FastAPI, ambiente Docker local e
  observabilidade OTel) estendem o escopo original; os requisitos 14, 15 e 16 têm tarefas
  próprias (16–20) sem alterar as tarefas 1–15 do núcleo.
- Cobertura de requisitos: **1** (4.2,7.1,10.1), **2** (3.1,3,4.2), **3** (4,7.2,7.3),
  **4** (4.1,4.2,4.2,7.5), **5** (5.1,6,8.1), **6** (6.1,8,10.5), **7** (7.1,7.2), **8**
  (8.1..8.6), **9** (9.1), **10** (10.1,13,12.6), **11** (11.1,10.2), **12.1/12.2**
  (property 6/8), **13.1/14** (docs), **14** (18.1,18.2,18.3,18.4,19.1,19.2,14.3 — FastAPI +
  Mangum/container + API Gateway + validação/erro→502 + P10/P11), **15**
  (16.1,16.2,20.1,20.2,20.3,20.4,20.5,20.6 — Ollama + docker-compose + AgentOS + `.env.local`
  + bootstrap LocalStack + P12), **16** (17.1..17.6 — OTel + `configure_agno_logging` +
  `request_id` + P12/P13), **17** (22.1,22.2,23.1,23.2,23.3,24.1,25.1 — fakes reutilizáveis +
  marcadores + golden set + scorers + eval de eficácia + eficiência operacional + smoke de
  sistema + P14) — todos os requisitos 1–17 têm ao menos uma tarefa.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "2.2", "3.1"] },
    { "id": 2, "tasks": ["2.3", "4.1", "5.1", "6.1"] },
    { "id": 3, "tasks": ["4.2", "5.2", "6.2", "7.1", "7.2", "16.1"] },
    { "id": 4, "tasks": ["7.3", "8.1", "16.2", "17.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "8.5", "8.6", "9.1", "17.2"] },
    { "id": 6, "tasks": ["9.2", "10.1", "17.3", "17.5"] },
    { "id": 7, "tasks": ["10.2", "12.1", "12.2", "13.1", "17.4", "17.6", "18.1"] },
    { "id": 8, "tasks": ["13.2", "14.1", "14.2", "18.2", "18.3", "18.4", "20.1", "20.3", "20.4", "20.5", "22.1", "22.2"] },
    { "id": 9, "tasks": ["19.1", "19.2", "20.2", "23.1", "23.2", "24.1"] },
    { "id": 10, "tasks": ["14.3", "20.6", "23.3", "25.1"] }
  ]
}
```
