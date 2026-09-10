# Requirements Document

## Introduction

O Agente 01 — Suporte com SLA e Fallback de Provider é um assistente de suporte de
primeiro nível que responde dúvidas de clientes fundamentadas na documentação do produto.
Além de dominar o agente Agno básico (persona, `instructions`, modos `run`/`stream`,
sampling), o agente é resiliente: expõe múltiplos providers de LLM atrás de uma única
`LLMPort`, com timeout configurável, retry com backoff, fallback automático para um
provider secundário e degradação graciosa quando todos falham. A entrega é CLI + função
chamável, em projeto isolado (`uv`, Python 3.9+, Agno v2), sem imports de outros agentes.

Estes requisitos são derivados do documento de design aprovado e do contrato de escopo
(RF-01..RF-05, RNF-01..RNF-05) e do charter do portfólio (quatro documentos obrigatórios).
Cada requisito é numerado para permitir rastreabilidade das propriedades de correção do
design no formato "Requirements X.Y" (número do requisito . número do critério).

O agente agora também contempla uma entrega serverless por HTTP como aplicação FastAPI
publicada via AWS API Gateway, um ambiente completo de desenvolvimento local em
`docker-compose` (com AgentOS Agno) e observabilidade com OpenTelemetry. Estas adições
(entrega serverless FastAPI, ambiente Docker local e observabilidade OTel) ESTENDEM, a
pedido explícito do usuário, o escopo original do §5 (que listava deploy em nuvem como
fora de escopo para o agente 01), enquanto o núcleo CLI + função chamável (Requisitos 1–13)
permanece inalterado.

O agente também define uma estratégia de testes automatizados e avaliação de eficiência
(Requisito 17), derivada da skill `agno-testing-strategy`, que separa a "eficiência" do
agente em três sentidos — operacional (latência, tokens, taxa de fallback), eficácia
(qualidade da resposta) e resiliência (SLA sob falha) — e organiza os testes em três
camadas de e2e (transporte determinístico, comportamento do agente com LLM real e sistema
real). Esta adição padroniza as práticas de teste já presentes nos Requisitos 1–16 e
acrescenta a avaliação de eficácia (golden set) e de eficiência operacional (orçamentos
sobre o log estruturado), sem alterar o comportamento funcional do núcleo.

## Glossary

- **Sistema**: o Agente 01 como um todo (CLI + caso de uso + adapters), quando o
  comportamento não é atribuível a um componente específico.
- **AnswerQuestion**: caso de uso da camada de aplicação que orquestra a resposta; depende
  apenas de uma `LLMPort` injetada e não conhece fallback.
- **LLMPort**: contrato (Protocol) de domínio com os métodos `ask` (modo run) e
  `ask_stream` (modo stream); implementado pelos adapters e pelo `FallbackLLM`.
- **FallbackLLM**: adapter de infraestrutura (Composite + Strategy) que implementa
  `LLMPort` e, por dentro, aplica timeout, retry, fallback e log estruturado sobre uma
  lista ordenada de providers.
- **Provider**: um provedor de LLM concreto (ex.: `openai`, `groq`, `ollama`), envolvido
  por um adapter (`OpenAIAdapter`, `GroqAdapter`).
- **Provider primário**: o primeiro provider na `provider_order`; **provider secundário**:
  os demais, tentados por fallback.
- **Answer**: value object imutável de domínio com `text`, `provider`, `prompt_tokens`,
  `completion_tokens`, `latency_ms` e `out_of_scope`.
- **Settings**: configuração tipada lida de variáveis de ambiente (`provider_order`,
  timeouts, tentativas, backoff).
- **Fora de escopo (out_of_scope)**: pergunta cuja resposta não está fundamentada na
  documentação do produto e que deve ser encaminhada a um humano.
- **Degradação graciosa**: retorno de mensagem clara ao usuário (sem stacktrace) quando
  todos os providers falham.
- **SLA por tentativa**: tempo máximo permitido para uma única chamada a um provider
  (`per_attempt_timeout_s`).
- **SLA total**: limite superior de tempo para a requisição inteira, incluindo fallback
  (`total_timeout_s`).
- **StructuredLogger**: componente de observabilidade que emite um log estruturado (JSON)
  por requisição.
- **question_hash**: hash da pergunta usado no log em vez do texto em claro, para reduzir
  exposição de PII.
- **FastAPI app**: aplicação FastAPI (rotas + validação Pydantic + docs OpenAPI) que expõe
  o agente por HTTP, reutilizando o caso de uso `AnswerQuestion` via `composition_root`.
- **Mangum**: adapter ASGI que traduz o evento do AWS Lambda para a app FastAPI; usado no
  caminho de deploy PADRÃO (`handler = Mangum(app)`).
- **AWS Lambda Web Adapter**: extension que permite ao Lambda invocar uma app HTTP normal
  (FastAPI com `uvicorn`) em imagem de container; usado no caminho de deploy ALTERNATIVO.
- **API Gateway**: serviço AWS que publica a app FastAPI por HTTP, cuidando de roteamento,
  autenticação, throttling e validação de payload na borda.
- **LocalStack**: emulador local dos serviços AWS (API Gateway, Lambda, DynamoDB, Secrets
  Manager, SSM) usado no ambiente de desenvolvimento em `docker-compose`.
- **Ollama**: provider de LLM local (custo zero, sem rede externa) usado no ambiente local,
  configurado via `OLLAMA_HOST`.
- **AgentOS**: servidor FastAPI embutido do Agno (porta 7777) que "veste" o agente para
  acompanhamento local de runs/sessions/traces, reutilizando o `composition_root`.
- **OTLP / OpenTelemetry Collector**: protocolo (OTLP) e coletor pelos quais o tracing OTel
  do Agno é exportado; o destino é definido por `OTEL_EXPORTER_OTLP_ENDPOINT`.
- **SigNoz**: backend de observabilidade OTLP tudo-em-um (traces, logs e métricas), padrão
  do ambiente local.
- **Grafana stack (Tempo/Loki/Prometheus)**: alternativa de observabilidade que separa os
  três sinais (Tempo para traces, Loki para logs, Prometheus para métricas) sob o Grafana.
- **Langfuse**: backend de tracing focado em LLM (custo, prompt/resposta por span), que pode
  subir como perfil adicional junto de qualquer backend de observabilidade.
- **request_id**: identificador único gerado no início de cada requisição, registrado tanto
  no log estruturado quanto no span OTel, para correlacionar log e trace.
- **Golden set (dataset de avaliação)**: conjunto curado e versionado de casos de teste
  (`dataset.jsonl`), cada um com pergunta e critério esperado (`in_scope`/`out_of_scope` e
  âncoras `must_mention`), usado para medir a eficácia do agente.
- **Scorer**: avaliador que atribui um veredito a uma resposta do agente; determinístico
  (regra sobre `escalated`/`must_mention`) ou LLM-as-judge (segundo modelo julga
  fundamentação, usado como sinal e nunca como gate rígido por caso).
- **Eval (avaliação de eficácia)**: execução do golden set com scorers, agregando um
  resultado por taxa (acurácia geral, `out_of_scope recall`) comparado a um threshold; roda
  fora do gate de PR (nightly/pré-release), nunca por commit.
- **Eficiência operacional**: latência (percentil p95/p99), tokens/custo e taxa de fallback
  por requisição, asseridos como orçamentos (budgets) sobre o log estruturado.
- **Orçamento (budget)**: limite superior configurável para uma métrica operacional
  (ex.: p95 de latência, média de tokens, taxa de fallback) que, se excedido, reprova o teste.
- **Camadas de e2e**: transporte determinístico (LLM fake, gate de PR), comportamento do
  agente (LLM real/Ollama, nightly) e sistema real (deploy vivo em LocalStack/AWS, smoke).
- **Marcador de teste**: rótulo `pytest` (`e2e`, `system`, `integration`) que separa os
  testes lentos/não-determinísticos/dependentes de rede do gate rápido de PR.

## Requirements

### Requirement 1: Resposta fundamentada na documentação do produto

**User Story:** Como cliente final, quero enviar uma pergunta em texto e receber uma
resposta fundamentada na documentação do produto, para que eu resolva minha dúvida sem
esperar por um atendente humano.

#### Acceptance Criteria

1. WHEN o Sistema recebe uma pergunta em texto não vazia, THE AnswerQuestion SHALL retornar uma resposta cujo conteúdo é fundamentado na documentação do produto injetada nas `instructions`/`context` do adapter.
2. WHEN um adapter converte a resposta do provider, THE adapter SHALL produzir um `Answer` cujo campo `text` é não vazio quando `out_of_scope` for `false`.
3. IF a pergunta recebida for vazia ou composta apenas de espaços em branco, THEN THE AnswerQuestion SHALL retornar uma mensagem de validação com `escalated = false` sem chamar a `LLMPort`.

### Requirement 2: Modos de resposta run e stream

**User Story:** Como responsável pela interface, quero escolher entre resposta completa
(`run`) e resposta incremental (`stream`), para que eu ofereça a melhor experiência de uso
em cada contexto.

#### Acceptance Criteria

1. WHERE o modo solicitado for `run`, THE AnswerQuestion SHALL retornar exatamente um `AnswerResponse` por meio de `LLMPort.ask`.
2. WHERE o modo solicitado for `run` e a chamada a `LLMPort.ask` falhar, THE AnswerQuestion SHALL tratar a falha conforme a degradação graciosa (Requisito 7), sem propagar stacktrace.
3. WHERE o modo solicitado for `stream`, THE AnswerQuestion SHALL produzir a resposta como uma sequência ordenada de zero ou mais fragmentos de texto por meio de `LLMPort.ask_stream`, preservando a ordem de emissão dos fragmentos.
4. IF o modo solicitado não for `run` nem `stream`, THEN THE AnswerQuestion SHALL rejeitar a solicitação sem invocar `LLMPort.ask` nem `LLMPort.ask_stream` e SHALL sinalizar o modo inválido.
5. WHEN um mesmo prompt é processado em ambos os modos para o mesmo provider e a mesma configuração de sampling, THE LLMPort SHALL produzir, no modo `stream`, uma sequência de fragmentos cuja concatenação é consistente em conteúdo com o campo `text` retornado pelo modo `run`.

### Requirement 3: Encaminhamento de perguntas fora de escopo sem alucinação

**User Story:** Como time de suporte, quero que o agente encaminhe para um humano quando a
pergunta estiver fora do escopo conhecido, para que o cliente não receba informação
inventada.

#### Acceptance Criteria

1. WHEN o modelo sinaliza que sua resposta não está fundamentada na documentação do produto fornecida nas `instructions`/`context`, THE adapter SHALL definir o campo `out_of_scope` do `Answer` como `true`.
2. WHEN o `Answer` retornado possui `out_of_scope = true`, THE AnswerQuestion SHALL retornar um `AnswerResponse` contendo uma mensagem de encaminhamento a atendente humano, `out_of_scope = true` e `escalated = true`.
3. IF a pergunta está fora do escopo conhecido, THEN THE AnswerQuestion SHALL retornar a mensagem de encaminhamento a atendente humano e SHALL suprimir qualquer conteúdo gerado sem fundamentação na documentação.
4. WHEN a AnswerQuestion retorna um `AnswerResponse` com `escalated = true` por fora de escopo, THE AnswerQuestion SHALL incluir na mensagem uma indicação legível de que a pergunta será transferida a um atendente humano, sem incluir texto do modelo não fundamentado.

### Requirement 4: Seleção de provider por configuração de ambiente

**User Story:** Como desenvolvedor, quero selecionar o provider de LLM por configuração de
ambiente, para que eu troque de provedor sem alterar código de domínio.

#### Acceptance Criteria

1. WHEN `load_settings` é executado, THE Settings SHALL derivar a `provider_order` a partir da variável de ambiente `MODEL_PROVIDER_ORDER` interpretada como lista separada por vírgulas, com ordem padrão contendo ao menos um provider.
2. WHEN a `provider_order` é alterada por variável de ambiente, THE composition_root SHALL montar o `FallbackLLM` na nova ordem sem exigir modificação em `domain/` ou `application/`.
3. THE domain SHALL não importar `agno` nem SDKs de provider (`openai`, `groq`).
4. THE application SHALL não importar `agno` nem SDKs de provider (`openai`, `groq`).
5. IF a `provider_order` referenciar um provider desconhecido (sem `ProviderConfig` correspondente), THEN THE load_settings SHALL levantar `ValueError` nomeando o provider desconhecido.
6. IF a `provider_order` resolvida resultar em lista vazia, THEN THE load_settings SHALL levantar `ValueError` indicando que ao menos um provider é obrigatório.

### Requirement 5: Fallback automático e registro do evento

**User Story:** Como responsável pelo SLA, quero fallback automático para um provider
secundário quando o primário falha ou expira, para que o cliente continue sendo atendido
e o evento fique registrado.

#### Acceptance Criteria

1. WHEN o provider primário retorna uma resposta dentro do `per_attempt_timeout_s` (padrão 10 segundos, configurável), THE FallbackLLM SHALL retornar a resposta do primário com `provider` igual ao provider primário e `used_fallback = false`.
2. IF o provider primário falhar ou exceder o `per_attempt_timeout_s` em todas as tentativas configuradas (`max_attempts_per_provider`, padrão 2), THEN THE FallbackLLM SHALL tentar automaticamente o próximo provider da `provider_order` na ordem definida.
3. WHEN um provider secundário retorna uma resposta dentro do `per_attempt_timeout_s` após esgotamento das tentativas do primário, THE FallbackLLM SHALL retornar o `Answer` desse provider com `provider` igual ao provider secundário e `used_fallback = true`.
4. WHEN ocorre um evento de fallback, THE StructuredLogger SHALL registrar o evento contendo a lista ordenada dos providers tentados e `used_fallback = true`.
5. IF todos os providers da `provider_order` falharem ou excederem o timeout em todas as tentativas configuradas, THEN THE FallbackLLM SHALL levantar `ProviderUnavailable` contendo a lista dos providers tentados na ordem em que foram tentados.
6. WHEN todos os providers falharem, THE StructuredLogger SHALL registrar o evento com a lista ordenada dos providers tentados, `used_fallback = true` e `result = "error"`.

### Requirement 6: SLA por tentativa e limite total de tempo

**User Story:** Como responsável pelo SLA, quero um timeout configurável por tentativa e um
limite superior de tempo total, para que a latência seja previsível mesmo com fallback.

#### Acceptance Criteria

1. THE Settings SHALL expor `per_attempt_timeout_s` configurável por ambiente, com valor padrão de 10 segundos.
2. WHEN uma tentativa de chamada a um provider atinge ou excede `per_attempt_timeout_s`, THE FallbackLLM SHALL abortar a tentativa levantando `TimeoutError` internamente e contá-la como falha.
3. THE Settings SHALL expor `total_timeout_s` configurável por ambiente, servindo de limite superior para a requisição inteira incluindo fallback.
4. IF `total_timeout_s` for configurado com valor menor que `per_attempt_timeout_s`, THEN THE Settings SHALL rejeitar a configuração e sinalizar erro indicando a restrição `total_timeout_s >= per_attempt_timeout_s`.
5. WHEN o tempo total decorrido atinge `total_timeout_s`, THE FallbackLLM SHALL interromper novas tentativas e levantar `ProviderUnavailable`, sem exceder `total_timeout_s` além da margem de uma verificação de tempo.

### Requirement 7: Retry com backoff e degradação graciosa

**User Story:** Como responsável pela resiliência, quero retry limitado com backoff por
provider e degradação graciosa ao final, para que falhas transitórias sejam absorvidas e o
usuário nunca veja um stacktrace.

#### Acceptance Criteria

1. THE Settings SHALL expor `max_attempts_per_provider` configurável por ambiente, com valor padrão de 2.
2. WHILE um provider ainda tem tentativas restantes após uma falha, THE FallbackLLM SHALL aguardar um intervalo de backoff exponencial calculado a partir de `backoff_base_s` antes da próxima tentativa.
3. WHEN um provider esgota `max_attempts_per_provider` tentativas sem sucesso, THE FallbackLLM SHALL prosseguir para o próximo provider na ordem de fallback.
4. IF o provider atual for o último da ordem e esgotar suas tentativas, THEN THE FallbackLLM SHALL sinalizar exaustão de todos os providers levantando `ProviderUnavailable`.
5. IF todos os providers falharem, THEN THE AnswerQuestion SHALL retornar um `AnswerResponse` contendo mensagem legível de indisponibilidade temporária e `escalated = true`, sem propagar exceção nem stacktrace ao chamador.

### Requirement 8: Registro de custo (tokens e provider por requisição)

**User Story:** Como responsável por custo, quero registrar tokens consumidos e provider
usado por requisição em log estruturado, para que eu possa analisar o custo de operação.

#### Acceptance Criteria

1. WHEN um adapter converte a resposta de um provider, THE adapter SHALL preencher `prompt_tokens` e `completion_tokens` no `Answer` como inteiros maiores ou iguais a zero.
2. WHERE o provider omite a contagem de tokens, THE adapter SHALL preencher `prompt_tokens` e `completion_tokens` com zero.
3. WHEN uma requisição é concluída com sucesso, THE StructuredLogger SHALL registrar, em log estruturado, o `provider` utilizado e os tokens consumidos (`prompt_tokens`, `completion_tokens` e `total_tokens`).
4. WHEN uma requisição resulta em erro após invocar ao menos um provider, THE StructuredLogger SHALL registrar o `provider` tentado.
5. THE Answer SHALL derivar `total_tokens` como a soma de `prompt_tokens` e `completion_tokens`.

### Requirement 9: Log estruturado por requisição (observabilidade)

**User Story:** Como operador do sistema, quero um log estruturado por requisição com os
metadados relevantes, para que eu possa observar e diagnosticar o comportamento em produção.

#### Acceptance Criteria

1. WHEN uma requisição é concluída (com sucesso ou falha), THE StructuredLogger SHALL emitir exatamente um log estruturado contendo `question_hash`, `provider`, `latency_ms`, tokens, `used_fallback`, `attempts` e `result`.
2. THE StructuredLogger SHALL registrar `latency_ms` como inteiro maior ou igual a zero em milissegundos, tokens como inteiro maior ou igual a zero no total e `attempts` como inteiro maior ou igual a um, e `used_fallback` como booleano.
3. WHERE o resultado da requisição é bem-sucedido, THE StructuredLogger SHALL registrar `result = "ok"`.
4. WHERE o resultado da requisição é uma falha total dos providers, THE StructuredLogger SHALL registrar `result = "error"`.
5. THE StructuredLogger SHALL registrar a pergunta somente como `question_hash` determinístico, nunca em texto em claro.

### Requirement 10: Segredos fora do código

**User Story:** Como responsável por segurança, quero que as chaves de API fiquem somente
em variáveis de ambiente e nunca no código, para que segredos não sejam versionados nem
expostos.

#### Acceptance Criteria

1. THE Sistema SHALL ler as chaves de API somente de variáveis de ambiente em tempo de execução, sem literais de chave no código-fonte.
2. THE ProviderConfig SHALL armazenar apenas o nome da variável de ambiente da chave (`api_key_env`), nunca o valor da chave.
3. THE projeto SHALL incluir `.env` no `.gitignore`.
4. THE projeto SHALL fornecer um arquivo `.env.example` presente, contendo apenas valores placeholder ou vazios, sem valores reais de chave.
5. THE StructuredLogger SHALL não registrar nenhuma chave de API em log.
6. IF uma variável de ambiente de chave exigida estiver ausente na inicialização, THEN THE Sistema SHALL abortar a inicialização do provider com erro nomeando a variável ausente, sem expor valores de chave.

### Requirement 11: Entrega como CLI e função chamável

**User Story:** Como usuário e como desenvolvedor, quero acessar o agente tanto por CLI
quanto por função chamável, para que ele seja usado interativamente e integrado por código.

#### Acceptance Criteria

1. WHEN a CLI é executada com uma pergunta de 1 a 4000 caracteres e um modo suportado, THE interface SHALL chamar o caso de uso via `composition_root` e imprimir a saída formatada.
2. IF a pergunta fornecida à CLI for vazia, ausente ou exceder 4000 caracteres, THEN THE interface SHALL rejeitar a solicitação com mensagem de validação, sem chamar o caso de uso.
3. IF o modo fornecido à CLI for inválido, THEN THE interface SHALL rejeitar a solicitação com mensagem indicando os modos suportados.
4. THE composition_root SHALL fornecer uma função chamável (`build_answer_question`) que monta o `FallbackLLM` a partir de `Settings` e injeta uma `LLMPort` no `AnswerQuestion`.
5. IF um erro de configuração ocorre durante `build_answer_question`, THEN THE composition_root SHALL abortar a montagem nomeando o valor de configuração ausente.
6. WHEN um erro de domínio ocorre, THE interface SHALL traduzi-lo em mensagem amigável ao usuário, sem exibir stacktrace.

### Requirement 12: Projeto isolado

**User Story:** Como responsável pela arquitetura do portfólio, quero que o agente seja um
projeto isolado, para que cada estudo seja reprodutível e livre de acoplamento entre agentes.

#### Acceptance Criteria

1. THE projeto SHALL residir em `agents/01-suporte-sla-fallback/` com `pyproject.toml` e ambiente virtual próprios gerenciados por `uv`.
2. THE projeto SHALL declarar Python 3.9 ou superior e Agno 2.x.
3. WHEN `uv` resolve as dependências do projeto, THE resolução SHALL concluir sem erros.
4. THE projeto SHALL não conter imports que referenciem outros agentes fora do diretório `agents/01-suporte-sla-fallback/`.
5. IF a camada `domain`, `application` ou `interface` importar tipos da Agno, THEN THE verificação de arquitetura SHALL falhar nomeando o módulo infrator.
6. THE tipos da Agno SHALL ser importados apenas pela camada `infrastructure`.

### Requirement 13: Documentos obrigatórios do agente

**User Story:** Como responsável pela documentação, quero os documentos obrigatórios do
agente entregues, para que o estudo seja explicável e a Definition of Done seja atendida.

#### Acceptance Criteria

1. THE projeto SHALL entregar um `README.md` no diretório do agente contendo as quatro seções obrigatórias (o que o agente estuda, como rodá-lo, o que observar e limitações).
2. THE projeto SHALL entregar um `DESENVOLVIMENTO.md` no diretório do agente descrevendo as decisões de arquitetura, incluindo a justificativa explícita de por que o fallback vive na camada `infrastructure` e não no caso de uso.
3. THE código-fonte SHALL incluir docstrings para funções, classes e módulos públicos.
4. THE código-fonte SHALL incluir comentários explicativos na lógica de fallback.
5. IF a documentação obrigatória ou qualquer uma de suas seções estiver ausente, THEN THE avaliação da Definition of Done SHALL marcar o item como não atendido, nomeando o que está faltando.

### Requirement 17: Estratégia de testes automatizados e avaliação de eficiência

**User Story:** Como responsável pela qualidade, quero uma estratégia de testes que separe
os três sentidos de "eficiência" do agente (operacional, eficácia e resiliência) em camadas
de e2e bem definidas, para que a qualidade seja medida de forma objetiva e reproduzível sem
que testes lentos ou não-determinísticos bloqueiem cada commit.

Derivado da skill `agno-testing-strategy`. Este requisito padroniza e complementa as
práticas de teste já expressas nos Requisitos 1–16 (testes determinísticos com fakes,
verificação estática, TestClient FastAPI, correlação `request_id`), acrescentando a
avaliação de eficácia por golden set e a asserção de eficiência operacional sobre o log
estruturado.

#### Acceptance Criteria

1. THE suíte de testes SHALL separar os testes em três camadas: transporte determinístico (sem LLM real, provider fake), comportamento do agente (LLM real ou Ollama) e sistema real (deploy vivo), de modo que apenas a camada de transporte determinístico componha o gate obrigatório de cada mudança.
2. THE projeto SHALL fornecer fakes reutilizáveis da `LLMPort` — um provider que retorna `Answer` fixo, um provider programável que falha um número configurável de vezes antes de responder, e um `clock` controlável — que não realizam acesso à rede.
3. WHEN os testes de resiliência do `FallbackLLM` são executados, THE suíte SHALL exercitar timeout por tentativa, backoff e o SLA total usando o `clock` controlável, sem `sleep` real e sem acesso à rede.
4. THE projeto SHALL fornecer um golden set versionado (`tests/eval/dataset.jsonl`) contendo casos `in_scope` (com âncoras `must_mention`) e casos `out_of_scope` que devem escalar, com ao menos 20 casos.
5. THE avaliação de eficácia SHALL aplicar ao menos um scorer determinístico (verificando `escalated` para `out_of_scope` e presença das âncoras `must_mention` para `in_scope`) e SHALL agregar o resultado como uma taxa comparada a um threshold, nunca como pass/fail por caso individual.
6. THE avaliação de eficácia SHALL medir o `out_of_scope recall` (fração de perguntas fora de escopo que resultaram em `escalated = true`) com um threshold próprio, mais alto que o da acurácia geral.
7. WHERE um scorer do tipo LLM-as-judge é usado, THE avaliação SHALL tratá-lo como sinal agregado por taxa e SHALL não usá-lo como gate rígido por caso individual.
8. THE avaliação de eficiência operacional SHALL derivar as métricas exclusivamente do log estruturado por requisição (`latency_ms`, tokens, `used_fallback`), sem instrumentação adicional, e SHALL asserir latência como percentil (p95 ou p99), nunca como média nem máximo.
9. THE avaliação de eficiência operacional SHALL asserir orçamentos configuráveis para o percentil de latência, para o consumo médio de tokens e para a taxa de fallback, reprovando quando qualquer orçamento é excedido.
10. WHEN qualquer requisição do conjunto avaliado é medida, THE avaliação de eficiência operacional SHALL verificar que a latência registrada não excede `total_timeout_s`.
11. THE suíte de testes SHALL marcar os testes de comportamento do agente com o marcador `e2e` e os testes de sistema real com o marcador `system`, de modo que o gate de PR possa ser executado excluindo esses marcadores.
12. WHERE a avaliação de eficácia é executada em integração contínua, THE avaliação SHALL usar o provider local Ollama como padrão (custo zero, sem rede externa) e SHALL reservar os providers reais para execução de pré-release, trocando apenas a variável de ambiente `MODEL_PROVIDER_ORDER` sem alterar código.
13. THE camada de sistema real SHALL fornecer um smoke test que verifica `GET /health` retornando `200` e `POST /chat` retornando `200` ou `502` contra o deploy (LocalStack ou AWS), sem depender de resposta determinística do modelo.
14. WHERE a avaliação de eficácia é executada, THE ambiente de eval SHALL fixar uma temperatura de sampling baixa e THE thresholds SHALL ser definidos com folga, de modo que ruído estatístico do modelo não reprove a suíte de forma intermitente.

### Requirement 14: Exposição da API com FastAPI publicada via API Gateway

**User Story:** Como integrador, quero a API exposta como aplicação FastAPI publicada atrás do AWS API Gateway, para que o agente seja consumido por HTTP com validação, docs OpenAPI e deploy serverless.

#### Acceptance Criteria

1. THE API SHALL ser implementada como uma aplicação FastAPI (rotas mais validação Pydantic), reutilizando o caso de uso `AnswerQuestion` por meio do `composition_root`, sem duplicar a camada de domínio.
2. THE FastAPI app SHALL expor uma rota de resposta `POST /chat` que aceita uma pergunta com comprimento entre 1 e 4000 caracteres (inclusive) e um modo pertencente ao conjunto finito de modos suportados definido no domínio, e uma rota de verificação de saúde `GET /health`.
3. IF uma requisição a `POST /chat` contém uma pergunta com comprimento fora do intervalo de 1 a 4000 caracteres, um modo não pertencente ao conjunto de modos suportados, ou um corpo malformado, THEN THE FastAPI app SHALL rejeitar a requisição com uma resposta de erro de validação de cliente, sem invocar o caso de uso `AnswerQuestion` e sem expor stacktrace ou detalhes internos.
4. THE FastAPI app SHALL ser publicada atrás do AWS API Gateway.
5. THE caminho de deploy padrão SHALL ser FastAPI com Mangum (`handler = Mangum(app)`), e THE caminho de deploy alternativo SHALL ser FastAPI em imagem de container com o AWS Lambda Web Adapter quando o pacote exceder o limite de Lambda Layers, mantendo a FastAPI app idêntica nos dois caminhos.
6. THE tipos da Agno SHALL permanecer confinados à camada `infrastructure`, e THE camada FastAPI SHALL depender do caso de uso e da porta, não de tipos da Agno.
7. WHEN um erro de domínio ocorre durante o processamento de uma rota, THE API SHALL retornar a resposta HTTP com o código de status `502`, contendo uma mensagem de erro que indica falha no processamento da requisição, sem stacktrace nem detalhes internos, preservando o comportamento sem estado da requisição.
8. WHEN uma requisição `GET /health` é recebida, THE FastAPI app SHALL retornar uma resposta HTTP com código de status `200` indicando que o serviço está operacional.
9. WHEN a aplicação FastAPI é iniciada, THE FastAPI app SHALL expor a documentação OpenAPI da API em uma rota de documentação acessível por HTTP.

### Requirement 15: Ambiente de desenvolvimento local com docker-compose e AgentOS

**User Story:** Como desenvolvedor, quero um `docker-compose` que suba todas as tecnologias de terceiros e um AgentOS para acompanhar o agente, para que eu desenvolva e observe localmente sem custo nem rede externa.

#### Acceptance Criteria

1. THE projeto SHALL fornecer um `docker-compose.yml` que sobe LocalStack (API Gateway, Lambda, DynamoDB, Secrets Manager e SSM), Ollama como provider de LLM local e um backend de observabilidade.
2. THE docker-compose SHALL usar perfis do Compose para selecionar os backends de observabilidade: o perfil padrão SHALL subir o SigNoz e o perfil alternativo SHALL subir Grafana com Tempo, Loki e Prometheus, e o Langfuse SHALL poder subir como perfil adicional.
3. IF o perfil padrão (SigNoz) e o perfil alternativo (Grafana com Tempo, Loki e Prometheus) forem ativados simultaneamente, THEN THE docker-compose SHALL sinalizar conflito de porta OTLP, de modo que apenas um backend de tracing OTLP ocupe a porta por vez.
4. THE projeto SHALL disponibilizar um AgentOS Agno na porta 7777 que reutiliza o `composition_root` do agente, sem duplicar a camada de domínio, para acompanhamento de runs, sessions e traces.
5. THE endpoints de serviços (AWS via `AWS_ENDPOINT_URL` do LocalStack, OTLP via `OTEL_EXPORTER_OTLP_ENDPOINT` e Ollama via `OLLAMA_HOST`) SHALL vir de variáveis de ambiente, de modo que o código do agente não mude entre local e produção.
6. THE projeto SHALL fornecer um arquivo `.env.local.example` versionado, sem valores reais, e SHALL manter `.env.local` fora do repositório por meio do `.gitignore`.
7. WHEN o LocalStack fica pronto, THE script de bootstrap SHALL criar os recursos esperados pelo agente, incluindo ao menos uma tabela DynamoDB e um segredo.
8. WHEN o script de bootstrap é reexecutado com os recursos já existentes, THE script de bootstrap SHALL concluir de forma idempotente, sem falhar por recurso já criado.

### Requirement 16: Observabilidade com OpenTelemetry integrada ao log estruturado

**User Story:** Como operador, quero tracing OpenTelemetry correlacionado ao log estruturado por requisição, para que eu possa investigar uma execução e reconstruir o que aconteceu.

#### Acceptance Criteria

1. THE tracing OpenTelemetry do Agno SHALL ser exportado via OTLP para o endpoint definido na variável de ambiente `OTEL_EXPORTER_OTLP_ENDPOINT`, sem qualquer alteração no código do agente ao trocar o valor dessa variável entre um destino local e um destino AWS.
2. IF a variável `OTEL_EXPORTER_OTLP_ENDPOINT` estiver ausente ou vazia, THEN THE Sistema SHALL desabilitar a exportação de tracing, registrar no log estruturado uma entrada de nível de aviso indicando exportação desabilitada por endpoint não configurado, e continuar o processamento da requisição sem interromper a execução do agente.
3. THE logger do Agno SHALL ser configurado por `configure_agno_logging` para emitir cada evento como uma única linha em formato JSON idêntico em estrutura de campos ao produzido pelo `StructuredLogger` da aplicação.
4. WHEN uma requisição é iniciada, THE Sistema SHALL gerar um `request_id` único por requisição e registrá-lo, de forma idêntica, tanto como campo no log estruturado quanto como atributo no span OpenTelemetry correspondente, permitindo correlacionar log e trace pelo mesmo valor de `request_id`.
5. THE tracing e o log SHALL registrar a pergunta somente como o valor `question_hash` e SHALL omitir ou redigir qualquer PII, nunca gravando a chave de API nem o texto da pergunta em claro, conforme exigido nos Requisitos 9 e 10.
6. WHERE o ambiente de execução é desenvolvimento, THE `debug_mode` do Agno SHALL estar habilitado.
7. THE `debug_mode` do Agno SHALL estar desabilitado por padrão em qualquer ambiente que não seja desenvolvimento.
8. THE configuração de observabilidade, incluindo tracing, `configure_agno_logging` e o exporter OTLP, SHALL residir exclusivamente na camada `infrastructure` e no `composition_root`, sem qualquer referência a essa configuração nas camadas de domínio e aplicação.
