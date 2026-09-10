# DESENVOLVIMENTO.md — Agente 01: Suporte com SLA e Fallback de Provider

Este documento explica as **decisões de arquitetura** por trás do agente 01 — o
"porquê" de cada escolha, os princípios e padrões aplicados, os trade-offs
assumidos e o que faríamos diferente em produção real. Ele complementa o
`README.md` (que cobre o que o agente estuda, como rodar, o que observar e as
limitações); aqui o foco é o raciocínio de design, não o passo a passo de uso.

---

## 1. Conceito estudado

O agente 01 tem dois objetivos de estudo que caminham juntos:

1. **O agente Agno básico**: montar um assistente de suporte com persona e regras
   via `instructions`, documentação do produto injetada via `context` (sem RAG),
   os dois modos de resposta (`run` completo e `stream` incremental) e os
   parâmetros de sampling.

2. **Resiliência de provider**: colocar múltiplos providers de LLM (`openai`,
   `groq`, `ollama`) atrás de **uma única abstração** e torná-los resilientes com
   timeout por tentativa, timeout total (SLA), retry com backoff exponencial,
   fallback automático para um provider secundário e degradação graciosa quando
   todos falham.

A tese central de estudo — e o fio condutor de todo o design — é uma frase:
**o caso de uso não sabe que existe fallback**. Para a camada de aplicação existe
apenas uma `LLMPort`. Toda a máquina de resiliência (tentar `openai`, dar timeout,
esperar backoff, tentar `groq`, logar o evento) mora atrás dessa porta, na
infraestrutura. Entender por que essa separação é a escolha correta — e não um
mero detalhe de organização — é o que o agente ensina.

---

## 2. Decisões de arquitetura por camada

A arquitetura é **ports & adapters** (hexagonal) com a dependência sempre
apontando para dentro: `interface → application → domain`, e a `infrastructure`
implementando as ports definidas pelo domínio (inversão de dependência). Tipos da
Agno nunca vazam para fora de `infrastructure` (e do `agentos/`).

```
interface  →  application  →  domain  ←  infrastructure
   (CLI/API)     (caso de uso)   (ports)     (adapters + FallbackLLM + config + obs)
                                     ↑
                            composition_root (o único que conhece o concreto)
```

### 2.1 `domain/` — regras puras, Python sem framework

Arquivos: `ports.py` (`Answer`, `LLMPort`), `exceptions.py` (`ProviderUnavailable`,
`OutOfScopeQuestion`).

Decisões:

- **`LLMPort` é um `Protocol` `@runtime_checkable`**, não uma classe base
  abstrata. Escolhemos conformidade **estrutural** (duck typing tipado): qualquer
  classe com `ask`/`ask_stream` satisfaz o contrato sem herança explícita. Isso
  desacopla os adapters do domínio — eles não precisam importar nada do domínio
  para "serem" uma `LLMPort` — e o `@runtime_checkable` permite `isinstance` em
  testes e no composition root.
- **`Answer` é um value object imutável (`frozen=True`)** com `total_tokens`
  derivado por `@property`. Imutabilidade evita mutação acidental ao propagar o
  resultado pelas camadas; a propriedade derivada garante que `total_tokens` nunca
  fique inconsistente com as parcelas.
- **Zero import de Agno/OpenAI/Groq aqui.** É o que mantém o domínio estável: ele
  não muda quando um SDK muda. Essa regra é verificada por um teste estático
  (Property 6 / Req 12.5, 12.6).

### 2.2 `application/` — orquestração da regra de negócio

Arquivos: `dto.py` (`Mode`, `AnswerRequest`, `AnswerResponse`), `use_cases/answer_question.py`.

Decisões:

- **`AnswerQuestion` recebe uma `LLMPort` injetada no construtor** e depende só
  dela. Ele valida a pergunta (não vazia, modo suportado), chama a porta, traduz
  `Answer` em `AnswerResponse`, encaminha perguntas fora de escopo a um humano e
  captura `ProviderUnavailable` devolvendo uma mensagem amigável (degradação
  graciosa). **Não há uma linha sobre timeout, retry, backoff ou "próximo
  provider" aqui** — e isso é intencional (ver seção 4).
- **DTOs separam a interface da assinatura interna do caso de uso.** A CLI e a API
  falam em `AnswerRequest`/`AnswerResponse`, não em tipos de domínio internos,
  o que deixa a borda livre para evoluir.
- **Detalhe honesto sobre `used_fallback`**: o caso de uso reporta
  `used_fallback=False` de forma conservadora. Uma única `Answer` só carrega
  `provider`, não "quem era o primário". Quem conhece a ordem — e portanto se
  houve fallback — é o `FallbackLLM`, que registra isso no **log estruturado**.
  Inferir fallback no caso de uso exigiria dar a ele a ordem dos providers, o que
  quebraria justamente a ignorância que queremos preservar (DIP). O trade-off
  consciente: a verdade sobre fallback vive no log, não no DTO de resposta.

### 2.3 `infrastructure/` — adapters concretos e a máquina de resiliência

Arquivos: `config.py` (`Settings`/`ProviderConfig`), `llm/*_adapter.py`,
`llm/fallback_llm.py`, `observability/logging.py`, `observability/tracing.py`.

Decisões:

- **Cada adapter (`OpenAIAdapter`, `GroqAdapter`, `OllamaAdapter`) envolve um
  `Agent` da Agno e implementa `LLMPort`.** É o **único** lugar onde tipos da Agno
  existem, e o import da Agno é **tardio** (dentro do método/construtor), de modo
  que os módulos de montagem permaneçam importáveis mesmo sem o SDK instalado.
  Cada adapter converte a resposta da Agno em `Answer`, extrai tokens/latência,
  marca `out_of_scope` e **traduz qualquer falha de rede/timeout/auth em
  `ProviderUnavailable`** — o mesmo contrato de erro para todos (LSP), o que
  permite ao `FallbackLLM` tratar todos os providers de forma uniforme.
- **`FallbackLLM` é o núcleo de resiliência** (detalhado na seção 3). Também
  implementa `LLMPort`.
- **`Settings`/`ProviderConfig` são tipados e lidos de env.** `ProviderConfig`
  guarda apenas o **nome** da env var da chave (`api_key_env`), nunca o valor.
- **Observabilidade fica confinada aqui**: `StructuredLogger` emite uma linha JSON
  por requisição; `tracing.py` inicializa o OTel, liga `configure_agno_logging` e
  gera o `request_id`. O domínio e a aplicação nunca referenciam tracing (Property
  12 / Req 16.8).

### 2.4 `interface/` — bordas finas (CLI e API FastAPI)

Arquivos: `cli/main.py`, `api/api.py`, `api/main.py`.

Decisões:

- **A borda valida cedo e traduz erros.** A CLI valida tamanho (1..4000) e modo
  antes de chamar o caso de uso; a API valida na borda com Pydantic. Nenhuma delas
  expõe stacktrace: a CLI traduz erros de domínio em mensagens amigáveis e a API
  traduz `DomainError` em HTTP `502` genérico.
- **A API não importa Agno.** Ela depende do caso de uso e dos DTOs, e monta o
  caso de uso **uma vez por processo/container** via `composition_root` (Property
  10 / Req 14.6). Isso é verificado por teste estático.

### 2.5 `composition_root.py` — o único que conhece o concreto

É a "raiz de composição": lê `Settings`, liga a observabilidade uma única vez,
constrói os adapters na ordem de `provider_order`, compõe o `FallbackLLM` e o
injeta em `AnswerQuestion`. Por ser o único lugar que conhece classes concretas,
é também o único ponto afetado quando adicionamos um provider — nem domínio nem
caso de uso mudam.

---

## 3. Por que cada port e adapter existe

- **`LLMPort` (port de domínio)** existe para ser o **ponto de inversão**: a
  aplicação depende dela; a infraestrutura a implementa. Sem essa port, o caso de
  uso importaria SDKs de provider e viraria refém deles.
- **`OpenAIAdapter` / `GroqAdapter`** existem para **traduzir** o mundo da Agno/SDK
  (respostas, contagem de tokens, erros de rede) para o vocabulário do domínio
  (`Answer`, `ProviderUnavailable`). Encapsulam o detalhe "como falar com este
  provider".
- **`OllamaAdapter`** existe como **provider de fallback local** (custo zero, sem
  rede externa) para o ambiente `docker-compose`. Como é só mais uma `LLMPort`,
  acrescentar `ollama` à `provider_order` (ex.: `openai,ollama`) não toca em
  domínio nem caso de uso — é a demonstração viva do OCP.
- **`FallbackLLM` (adapter composto)** existe para **concentrar a resiliência** num
  único ponto atrás da port, em vez de espalhá-la pelo caso de uso ou pelos
  adapters. Ele é a razão de o caso de uso poder ignorar que há vários providers.
- **`StructuredLogger` / `tracing`** existem para dar **observabilidade** sem
  poluir o domínio: o custo e a latência viajam no `Answer`, mas o *registro* deles
  (e a correlação log↔trace via `request_id`) acontece na infraestrutura.

---

## 4. Por que o fallback vive na infraestrutura e não no caso de uso

Esta é a decisão de arquitetura mais importante do agente, e a justificativa
explícita exigida pela Definition of Done (Req 13.2).

**A regra:** o caso de uso `AnswerQuestion` orquestra a **regra de negócio** —
"responder a pergunta do cliente fundamentado na documentação, encaminhar a um
humano quando fora de escopo, nunca vazar stacktrace". Fallback, timeout, retry e
backoff **não são regra de negócio**: são **resiliência de infraestrutura**, um
detalhe de *como* falamos com um provedor externo instável. Por isso vivem no
`FallbackLLM`, atrás da `LLMPort`.

**Por que isso é o certo (o que ganhamos):**

- **Separação de responsabilidades (SRP).** O caso de uso tem uma razão para
  mudar: mudou a regra de negócio do atendimento. A máquina de resiliência tem
  outra: mudou a política de SLA/retry/ordem de providers. Misturar as duas faria
  o caso de uso mudar por dois motivos distintos.
- **Aberto/Fechado (OCP).** Se o fallback vivesse no caso de uso, ele conteria a
  lógica "tente `openai`; se falhar, tente `groq`; se falhar, tente `ollama`" — e
  **acrescentar um terceiro provider exigiria editar o caso de uso**. Com o
  fallback atrás da porta, adicionar um provider é acrescentar um adapter à lista
  no composition root. O caso de uso fica fechado para modificação e aberto para
  extensão.
- **Inversão de Dependência (DIP).** O caso de uso depende da abstração `LLMPort`.
  Ele não distingue um adapter simples de um `FallbackLLM` (Composite) — ambos
  "são" uma `LLMPort`. A ligação ao concreto acontece só no composition root.
- **Testabilidade.** Como o caso de uso só vê uma `LLMPort`, ele é testado com um
  `FakeLLM` em memória — **sem rede, sem timeout real, sem sleep**. E a resiliência
  (a parte difícil de testar) é isolada no `FallbackLLM`, testável com adapters
  fake e um `FakeClock` injetado, também sem rede. Se o fallback estivesse no caso
  de uso, todo teste de regra de negócio arrastaria a complexidade de timeout e
  concorrência.
- **Coesão do log.** Quem tenta os providers na ordem é quem sabe se houve
  fallback — então é o `FallbackLLM` quem emite o log estruturado com `attempts` e
  `used_fallback`. Colocar isso no caso de uso o forçaria a conhecer a ordem dos
  providers, quebrando a ignorância que buscamos.

**O que aconteceria se colocássemos no caso de uso (o anti-padrão):** o caso de uso
importaria os SDKs (ou pelo menos a lista de providers), conteria loops de retry e
`try/except` de timeout, ficaria acoplado a "quantos providers existem" e a
detalhes de `concurrent.futures`, e seus testes precisariam simular tempo e rede.
Ele deixaria de ser "a regra de negócio" para virar "a regra de negócio + um mini
orquestrador de resiliência" — violando SRP e OCP de uma só vez.

---

## 5. Composite + Strategy, OCP e DIP no `FallbackLLM`

O `FallbackLLM` é, ao mesmo tempo, dois padrões de projeto clássicos:

- **Composite** — ele *é uma* `LLMPort` (expõe `ask`/`ask_stream`) e, por dentro,
  é **composto por uma lista ordenada de outras `LLMPort`** (os adapters). Como o
  composto satisfaz o mesmo contrato dos componentes, o cliente (o caso de uso)
  trata "o todo" e "a parte" de forma uniforme.
- **Strategy** — cada provider da lista é uma **estratégia** de obter a resposta.
  O `FallbackLLM` percorre as estratégias na ordem de preferência e escolhe a
  próxima quando a atual falha (timeout ou `ProviderUnavailable`).

Como isso encadeia com os princípios:

- **OCP**: a ordem da lista *é* a política de fallback. Trocar `openai,groq` por
  `groq,openai`, ou acrescentar `ollama`, é mudar uma variável de ambiente —
  nenhum código de domínio, aplicação ou do próprio `FallbackLLM` muda.
- **DIP**: o `FallbackLLM` depende de `LLMPort` (a abstração), não de adapters
  concretos. Ele nem sabe se está falando com OpenAI ou Ollama.
- **LSP**: todos os adapters honram o mesmo contrato de erro (`ProviderUnavailable`)
  e a mesma forma de `Answer`, então o `FallbackLLM` os substitui uns pelos outros
  sem casos especiais.

Detalhes do algoritmo (do `fallback_llm.py`, com comentários explicativos no
código conforme Req 13.4):

- **Timeout por tentativa** via `concurrent.futures.ThreadPoolExecutor` +
  `future.result(timeout=deadline)`. O `deadline` de cada tentativa é
  `min(per_attempt_timeout_s, restante_do_total)`, para nunca estourar o SLA total
  esperando por uma única tentativa lenta.
- **SLA total** verificado **antes** de cada nova tentativa; ao atingir o teto,
  interrompe e levanta `ProviderUnavailable` — sem exceder o total além da margem
  de uma verificação de tempo.
- **Retry com backoff exponencial** `backoff_base_s * 2^(n-1)` entre tentativas do
  mesmo provider.
- **`used_fallback` vira `True` a partir do 2º provider**; `attempts` acumula os
  nomes na ordem tentada e alimenta tanto o log quanto o `ProviderUnavailable`.

---

## 6. Trade-offs e alternativas consideradas

- **Threads para timeout (escolhido) vs. `asyncio` vs. `signal.alarm`.** Usamos
  `ThreadPoolExecutor` + `future.result(timeout=...)` porque é síncrono, simples de
  raciocinar e funciona em qualquer plataforma. Limitação assumida: em CPython uma
  thread bloqueada em I/O de rede **não é interrompível de fora** — por isso o SDK
  do provider também recebe seu próprio timeout de rede como **defesa em
  profundidade**, e o executor é fechado com `wait=False` para não bloquear o
  chamador. `asyncio` daria cancelamento cooperativo real, mas contaminaria toda a
  pilha (caso de uso e CLI teriam de virar async) — custo alto para um agente de
  estudo síncrono. `signal.alarm` só funciona na thread principal e no Unix.
- **Streaming materializado dentro do timeout (trade-off consciente).** No modo
  `stream`, o worker consome o iterador do provider e devolve a lista de chunks
  dentro do prazo da tentativa. Isso mantém o mecanismo de timeout **idêntico**
  entre `run` e `stream`, ao custo de materializar a resposta antes de emitir — o
  que sacrifica o *time-to-first-token* real para respostas muito grandes. Em
  produção, o caminho de container + AWS Lambda Web Adapter suporta streaming real
  ponta a ponta.
- **`Protocol` (escolhido) vs. ABC.** `Protocol` evita herança e acoplamento; ABC
  daria checagem em tempo de construção, mas forçaria os adapters a herdar do
  domínio.
- **`used_fallback` no log (escolhido) vs. no `AnswerResponse`.** Já discutido na
  seção 2.2: manter a verdade sobre fallback no log preserva a ignorância do caso
  de uso; o DTO reporta conservadoramente.
- **Doc do produto via `instructions`/`context` (escolhido) vs. RAG.** RAG está
  **fora de escopo** deste agente (pertence a agentes posteriores). Injeção estática
  é suficiente para exercitar o arranjo; a persona e a FAQ atuais são placeholders
  de estudo.

---

## 7. Fatores de produção aplicados

Mesmo sendo um agente de estudo, os fatores de produção da skill de arquitetura
foram aplicados e são verificáveis:

- **Timeout duplo**: por tentativa (`per_attempt_timeout_s`, default 10s) e total
  (`total_timeout_s`, default 25s), garantindo latência previsível mesmo com
  fallback (Property 4 / Req 6.5).
- **Retry com backoff exponencial** limitado por `max_attempts_per_provider`
  (default 2), evitando martelar um provider degradado (Req 7.2).
- **Fallback automático** com registro do evento (`attempts`, `used_fallback`) no
  log (Req 5.x).
- **Degradação graciosa**: quando todos falham, o usuário recebe uma mensagem clara
  e a pergunta é marcada para encaminhamento — nunca um stacktrace (Req 7.5).
- **Log estruturado (JSON), uma linha por requisição**, com `question_hash`,
  `provider`, `latency_ms`, tokens, `used_fallback`, `attempts`, `result` e
  `request_id` (Req 9).
- **Observabilidade OTel trocável por env**: `OTEL_EXPORTER_OTLP_ENDPOINT` define o
  destino do tracing; ausente/vazio **desabilita** o tracing com aviso, sem
  interromper a requisição. O `request_id` aparece idêntico no log e no span
  (`app.request_id`), correlacionando os dois (Property 13 / Req 16.4).
- **Segredos fora do código**: chaves só via env; `ProviderConfig` guarda o *nome*
  da env var, nunca o valor; `.env` no `.gitignore` e `.env.example` só com
  placeholders. A montagem aborta nomeando a env var ausente, sem expor valores
  (Property 8 / Req 10).
- **Privacidade (PII)**: a pergunta nunca é logada em claro — apenas como
  `question_hash` (SHA-256); nenhuma chave transita pelos logs (Req 9.5, 10.5).
- **Projeto isolado** (`uv`, Python 3.9+, Agno v2), sem imports de outros agentes.

---

## 8. Deploy da API, ambiente local e observabilidade por env

Esta seção explica o **porquê** das escolhas de entrega — os dois caminhos de deploy
da app FastAPI, como o ambiente local espelha a produção e onde (e por que) a
observabilidade é ligada. O passo a passo de uso está no `README.md` (seções 2 e 4);
aqui o foco é o raciocínio de design.

### 8.1 Uma app FastAPI, dois caminhos de deploy

A borda HTTP (`interface/api/api.py`) define **uma única** `app` FastAPI: valida na
borda com Pydantic, reutiliza o caso de uso via `composition_root` e traduz
`DomainError` em `502`. Essa mesma `app` é publicada atrás do AWS API Gateway por
**dois caminhos**, e a decisão de qual usar é de infraestrutura — o código do agente
não muda entre eles (Req 14.4, 14.5).

- **Padrão — Mangum + Lambda + Lambda Layer (`template.yaml`/SAM).** O handler
  `suporte.interface.api.main.handler` é apenas `Mangum(app)`: uma ponte que traduz o
  evento do API Gateway para uma requisição ASGI processada pela `app`. As
  dependências pesadas (`agno`, `fastapi`, `mangum`, SDKs de provider, OpenTelemetry)
  ficam num **Lambda Layer**, mantendo o pacote da função enxuto e o deploy rápido. O
  `Timeout` da função é 29s (teto de integração do API Gateway REST), com o SLA total
  do agente (`LLM_TOTAL_TIMEOUT`, default 25s) cabendo com folga nessa janela.
  **Por que é o padrão:** é o caminho mais simples e barato de operar; nenhuma imagem
  de container para construir/publicar, e o cold start é menor.

- **Alternativo — container + AWS Lambda Web Adapter (`Dockerfile`).** A **mesma** app
  sobe com `uvicorn` dentro de uma imagem de container; o AWS Lambda Web Adapter
  (extension em `/opt/extensions`) recebe cada invocação do Lambda e a encaminha como
  uma requisição HTTP para o `uvicorn` local. **Não há Mangum aqui** — a ponte é o
  Web Adapter. **Quando usar:** quando o pacote de deploy excede o limite de **250 MB**
  descompactado dos Lambda Layers (imagens de container toleram até 10 GB), ou quando
  se quer **streaming real** ponta a ponta (o caminho Mangum materializa a resposta;
  ver seção 6). O trade-off é operacional: build/push de imagem e cold start maior.

O ponto de design é que a escolha entre os dois é **substituição na borda de
infraestrutura**, não uma bifurcação de código. As rotas, a validação e a tradução de
erros são idênticas porque ambos os caminhos importam a mesma `app`. Nenhum dos dois
arquivos faz deploy sozinho — são receitas de infraestrutura; o deploy real
(`sam build`/`sam deploy` ou build/push da imagem) é um passo separado, fora do escopo
deste agente de estudo.

### 8.2 O ambiente local espelha a produção — endpoints por env

O `docker-compose.yml` sobe as tecnologias de terceiros **emuladas** (LocalStack para
AWS, Ollama como provider local, e um backend de observabilidade OTLP) mais o
**AgentOS** na porta 7777 para acompanhamento. A chave de design é que o agente lê
todos os destinos externos do **ambiente**, nunca hardcoded (Req 15.5):

- `AWS_ENDPOINT_URL` → LocalStack local (`http://localstack:4566`) vs. AWS real (ausente).
- `OLLAMA_HOST` → Ollama do Compose vs. host real.
- `OTEL_EXPORTER_OTLP_ENDPOINT` → collector local (SigNoz/OTel Collector) vs. collector
  da ADOT layer em produção.
- `MODEL_PROVIDER_ORDER` → ex.: `openai,ollama` local (fallback custo zero) vs.
  `openai,groq` em produção.

Como esses valores são lidos por `load_settings()`/`composition_root` a partir de env,
**o mesmo binário roda local e em produção** — muda-se a configuração, não o código.
É a mesma filosofia de "config por env" que o `template.yaml` expõe como *parameters*
do SAM. O AgentOS é **ferramenta de desenvolvimento** (runs/sessions/traces); ele
reusa `build_agent_for_agentos()` do `composition_root` e **não** duplica domínio —
e o deploy de produção não o utiliza (produção é FastAPI + API Gateway).

A observabilidade local é selecionada por **perfil do Compose** (SigNoz por padrão,
`--profile grafana` para a stack Grafana+Tempo+Loki+Prometheus, `--profile langfuse`
como adicional). SigNoz e o OTel Collector do perfil `grafana` disputam de propósito
a porta OTLP `4317` — um único backend OTLP por vez — o que sinaliza o conflito em vez
de esconder um erro de configuração.

### 8.3 Observabilidade ligada só na infraestrutura, trocada por env

Toda a observabilidade baseada em tracing vive em
`infrastructure/observability/tracing.py` e é ligada **uma única vez** pelo
`composition_root` (Req 16.8). `domain/` e `application/` **nunca** referenciam
tracing, `configure_agno_logging` ou o exporter OTLP — o que é verificado por teste
estático (Property 12). O `composition_root` chama `init_tracing(settings.otel_endpoint,
debug_mode=...)` ao montar o agente; a partir daí:

- **Endpoint por env, sem mudar código.** `init_tracing` recebe o valor de
  `OTEL_EXPORTER_OTLP_ENDPOINT`. Apontar o tracing para SigNoz, Grafana ou para a ADOT
  layer em produção é trocar essa variável. Se o endpoint estiver **ausente/vazio**,
  o tracing é **desabilitado** com um aviso no log estruturado e a requisição
  **prossegue** normalmente (Req 16.2) — observabilidade nunca é um ponto de falha do
  caminho de resposta.
- **Imports preguiçosos e tolerantes.** OpenTelemetry e Agno são importados dentro das
  funções, sob `try/except ImportError`. O núcleo/CLI funciona mesmo sem os pacotes de
  tracing instalados; na falta deles, apenas avisamos e seguimos.
- **`configure_agno_logging` unifica o formato.** Ligamos o logger do Agno ao **mesmo**
  logger JSON de uma linha do `StructuredLogger`, para que os eventos do Agno saiam com
  a estrutura idêntica à do resto da aplicação (Req 16.3). O `debug_mode` do Agno é
  habilitado **só em dev** (via `APP_ENV`), desligado por padrão fora de dev
  (Req 16.6/16.7).
- **Correlação log ↔ trace via `request_id`.** `new_request_id()` gera um id opaco
  (hex de UUID, sem PII) no início da requisição; ele é gravado como **campo** no log
  estruturado e anexado ao span OTel atual como atributo `app.request_id`
  (`set_request_id_on_span`). O **mesmo** valor nos dois lugares permite pular do log
  para o trace e vice-versa (Property 13 / Req 16.4). A pergunta nunca aparece em
  claro — só como `question_hash` — e nenhuma chave transita por log ou trace
  (Req 16.5).
- **OTel → X-Ray em produção.** No caminho Mangum/SAM, `Tracing: Active` liga o X-Ray
  na função e no gateway; a ADOT (AWS Distro for OpenTelemetry) layer, anexada à
  função, recebe os spans OTel exportados via OTLP para o collector local
  (`http://localhost:4317`) e os encaminha ao X-Ray — **sem** alterar o código do
  agente (só configuração no `template.yaml`).

O ganho de design: observabilidade é um **detalhe de infraestrutura plugável**. Trocar
o backend (SigNoz ↔ Grafana ↔ X-Ray) ou desligá-la é uma mudança de ambiente, não de
código, e jamais compromete a resiliência do caminho de resposta.

---

## 9. Como testar e o que observar

A estratégia de testes (skill `agno-testing-strategy`, Req 17) separa três sentidos
de "eficiência" — **operacional** (latência/tokens/taxa de fallback), **eficácia**
(qualidade) e **resiliência** (SLA sob falha) — em três camadas de e2e. Regra de
ouro: **o LLM real fica fora do caminho determinístico**.

- **Gate de PR (rápido, sem rede)**:
  `uv run pytest -m "not e2e and not system and not integration"`.
  Exercita o caminho completo com providers fake (`FakeLLM`,
  `ProgrammableAdapter`) e um `FakeClock` injetado — valida ordem de fallback,
  timeout, backoff, SLA total, degradação graciosa, observabilidade e o
  isolamento de arquitetura (imports proibidos em `domain`/`application`).
- **Nightly (`-m e2e`)**: eval de eficácia sobre o golden set (`tests/eval/`) com
  Ollama por padrão (custo zero) — acurácia geral e `out_of_scope recall` agregados
  por **taxa** contra thresholds, nunca pass/fail por caso.
- **Pré-release (`-m system`)**: smoke contra o deploy (LocalStack/AWS):
  `GET /health` → 200 e `POST /chat` → 200 ou 502.
- **Eficiência operacional**: asserida **sobre o log estruturado** — latência por
  **percentil** (p95/p99, nunca média/máximo), tokens médios e taxa de fallback
  como orçamentos; nenhuma latência acima de `total_timeout_s`.

O que observar em uma execução (também descrito no `README.md`, seção 3): o campo
`provider` (quem respondeu), `latency_ms`, os tokens, `used_fallback`+`attempts`
(derrube o primário com chave inválida ou timeout forçado e veja o secundário
respondendo e o fallback registrado) e `result` (`ok`/`error`). Com OTel ligado,
correlacione log e trace pelo `request_id`.

---

## 10. O que faria diferente em produção real

Este é um agente de estudo; num sistema de produção real eu ajustaria:

- **RAG em vez de doc estática**: substituir a FAQ injetada em `context` por
  recuperação de uma base de conhecimento versionada, com citações — reduzindo
  alucinação e mantendo a resposta fundamentada quando a doc cresce.
- **Persona e prompt versionados**: tirar `_DEFAULT_INSTRUCTIONS`/`_PRODUCT_CONTEXT`
  do código e movê-los para arquivos/config versionados, com revisão e testes de
  regressão de prompt.
- **Cancelamento real de tentativas**: adotar `asyncio` (ou clientes HTTP com
  cancelamento nativo) para que um timeout realmente aborte a chamada de rede, em
  vez de depender do timeout do SDK como defesa em profundidade.
- **Circuit breaker e health-aware routing**: além de retry/fallback, abrir um
  circuito para um provider consistentemente degradado (parar de tentá-lo por uma
  janela) e rotear pela saúde/latência recente, não só por ordem estática.
- **Cache e idempotência**: cache de respostas para perguntas repetidas e chaves de
  idempotência por requisição, reduzindo custo e latência.
- **Segredos em cofre gerenciado**: ler chaves de AWS Secrets Manager/SSM (já
  emulados no LocalStack local) com rotação, em vez de `.env` no ambiente.
- **Rate limiting e backpressure na borda** (API Gateway/FastAPI), além de auth e
  quotas por cliente.
- **SLOs e alertas** sobre as métricas já logadas (p95 de latência, taxa de
  fallback, `out_of_scope recall`), com painéis e alarmes — fechando o ciclo entre
  a observabilidade que já emitimos e a operação.
- **Streaming real ponta a ponta** no deploy (container + AWS Lambda Web Adapter),
  eliminando a materialização de chunks descrita na seção 6.
