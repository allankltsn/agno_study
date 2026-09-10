---
name: agno-project-architecture-solid
description: Estrutura de pastas padronizada, principios SOLID e fluxo de desenvolvimento produtivo serverless (Lambda + API Gateway, libs em Layers, deploy com AWS SAM) para projetos de agentes Agno em Python. Use ao criar um novo projeto, organizar modulos, refatorar codigo, revisar arquitetura ou estruturar o ciclo de dev/deploy serverless.
---

# Arquitetura de Projeto + SOLID

Padrao de estrutura de pastas e principios de design para projetos faceis de
refatorar, bem segmentados e com baixo acoplamento. Aplica-se aos agentes Agno
deste workspace (CLI, workflows, API serverless).

## Objetivos

- Segmentacao clara: cada pasta tem uma unica responsabilidade.
- Refatoracao simples: trocar provider/DB/entrada nao quebra o dominio.
- Testabilidade: a logica de negocio nao depende de infraestrutura.
- Baixo acoplamento e alta coesao (base para aplicar SOLID).

## Camadas (ports & adapters)

A dependencia aponta sempre para DENTRO: interface -> application -> domain.
A infraestrutura implementa interfaces definidas por dentro (inversao de dependencia).

```
domain          <- regras de negocio puras (sem libs externas)
application     <- casos de uso; orquestra o dominio via interfaces (ports)
infrastructure  <- adapters concretos (LLM, DB, APIs, arquivos)
interface       <- pontos de entrada (CLI, HTTP/Lambda, worker)
```

Regra de ouro: `domain` nao importa nada de `infrastructure` nem de frameworks.

## Estrutura de pastas padrao

```
<projeto>/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── <pacote>/
│       ├── __init__.py
│       ├── domain/                 # regras puras, sem dependencias externas
│       │   ├── models.py           # entidades / value objects
│       │   ├── exceptions.py       # erros de dominio
│       │   └── ports.py            # interfaces (protocols) que o dominio exige
│       ├── application/            # casos de uso (orquestracao)
│       │   ├── use_cases/
│       │   │   └── answer_question.py
│       │   └── dto.py              # entrada/saida dos casos de uso
│       ├── infrastructure/         # implementacoes concretas (adapters)
│       │   ├── llm/
│       │   │   └── agno_agent.py   # adapta Agno a uma port do dominio
│       │   ├── persistence/
│       │   │   └── dynamo_repo.py  # implementa um repositorio (port)
│       │   ├── secrets/
│       │   │   └── secrets_manager.py
│       │   └── config.py           # leitura de env / settings
│       ├── interface/              # pontos de entrada (adapters de entrega)
│       │   ├── http/
│       │   │   └── lambda_handler.py
│       │   ├── cli/
│       │   │   └── main.py
│       │   └── worker/
│       │       └── consumer.py
│       └── composition_root.py     # monta tudo: injeta dependencias
└── tests/
    ├── domain/
    ├── application/
    └── infrastructure/
```

Notas:
- Layout `src/` (codigo em `src/<pacote>/`) evita imports acidentais e facilita
  empacotamento/teste.
- `ports.py` guarda as interfaces (via `typing.Protocol` ou ABC) das quais o
  dominio e os casos de uso dependem; a infraestrutura as implementa.
- `composition_root.py` e o UNICO lugar que conhece implementacoes concretas e
  as injeta nos casos de uso (nao espalhe instanciacao pelo codigo).
- Espelhe a arvore em `tests/` para achar o teste de cada modulo rapidamente.

## Onde cada tipo de codigo mora

- Regra de negocio pura -> `domain/`
- Fluxo/orquestracao de uma tarefa -> `application/use_cases/`
- Chamada a Agno/LLM, DB, HTTP externo, segredos -> `infrastructure/`
- Receber request (HTTP/CLI/evento) e traduzir para caso de uso -> `interface/`
- Ligar implementacoes concretas aos casos de uso -> `composition_root.py`

## Principios SOLID

Cinco principios para codigo coeso, flexivel e facil de manter.

### S - Single Responsibility Principle (Responsabilidade Unica)

Uma classe/modulo deve ter apenas UM motivo para mudar.

- Sintoma de violacao: uma classe que faz parsing, chama o LLM E grava no banco.
- Como aplicar: separe cada responsabilidade. O caso de uso orquestra; o adapter
  do LLM so fala com o LLM; o repositorio so persiste.

```python
# Ruim: uma classe faz tudo
class Assistant:
    def handle(self, text):
        data = self._parse(text)          # parsing
        answer = self._call_openai(data)  # LLM
        self._save_to_db(answer)          # persistencia
        return answer

# Bom: responsabilidades separadas, orquestradas pelo caso de uso
class AnswerQuestion:
    def __init__(self, llm, repo):
        self._llm = llm
        self._repo = repo
    def execute(self, question: str) -> str:
        answer = self._llm.ask(question)
        self._repo.save(question, answer)
        return answer
```

### O - Open/Closed Principle (Aberto/Fechado)

Aberto para extensao, fechado para modificacao. Adicione comportamento novo sem
alterar codigo existente e ja testado.

- Como aplicar: dependa de abstracoes (ports) e adicione novas implementacoes.
  Trocar OpenAI por Groq = nova classe que implementa a mesma port, sem tocar no
  caso de uso.

```python
class LLMPort(Protocol):
    def ask(self, prompt: str) -> str: ...

class OpenAIAdapter:   # implementacao existente
    def ask(self, prompt: str) -> str: ...

class GroqAdapter:     # extensao: nova implementacao; nada muda no caso de uso
    def ask(self, prompt: str) -> str: ...
```

### L - Liskov Substitution Principle (Substituicao de Liskov)

Uma implementacao deve poder substituir sua abstracao sem quebrar o comportamento
esperado. Nao viole o contrato da interface.

- Sintoma de violacao: um adapter que "implementa" a port mas lanca excecao em um
  metodo, muda o tipo de retorno ou exige pre-condicoes mais fortes.
- Como aplicar: toda implementacao de uma port respeita o mesmo contrato (mesmos
  tipos de retorno, mesmas garantias, mesmos erros previstos).

### I - Interface Segregation Principle (Segregacao de Interfaces)

Prefira varias interfaces pequenas e especificas a uma grande. Ninguem deve ser
forcado a depender de metodos que nao usa.

- Sintoma de violacao: uma port `Repository` com 15 metodos, mas o caso de uso so
  usa `save` e `get`.
- Como aplicar: quebre em ports focadas (`ReadPort`, `WritePort`) para que cada
  consumidor dependa so do que precisa.

```python
class SessionReader(Protocol):
    def get(self, session_id: str) -> Session | None: ...

class SessionWriter(Protocol):
    def save(self, session: Session) -> None: ...
# Um adapter pode implementar as duas; consumidores dependem so da que usam.
```

### D - Dependency Inversion Principle (Inversao de Dependencia)

Modulos de alto nivel (dominio/casos de uso) nao dependem de modulos de baixo
nivel (infra); ambos dependem de abstracoes. Detalhes dependem de abstracoes.

- Como aplicar: o caso de uso recebe uma port por injecao; a infraestrutura
  implementa a port. A ligacao acontece no `composition_root`.

```python
# composition_root.py - unico lugar que conhece as classes concretas
from .infrastructure.llm.agno_agent import AgnoLLMAdapter
from .infrastructure.persistence.dynamo_repo import DynamoSessionRepo
from .application.use_cases.answer_question import AnswerQuestion

def build_answer_question() -> AnswerQuestion:
    llm = AgnoLLMAdapter()            # detalhe concreto
    repo = DynamoSessionRepo()        # detalhe concreto
    return AnswerQuestion(llm=llm, repo=repo)   # injeta abstracoes
```

## Aplicando ao Agno (pratico)

- Envolva o `Agent` da Agno em um adapter (`infrastructure/llm/agno_agent.py`) que
  implementa uma `LLMPort` do dominio. Casos de uso nao importam Agno direto.
- Trocar provider (OpenAI/Groq/Ollama/Bedrock) = novo adapter, sem mexer no dominio.
- Sessions/memory ficam atras de uma port de repositorio; a implementacao pode ser
  SQLite (estudo) ou DynamoDB (serverless) sem afetar os casos de uso.
- Handlers (CLI, Lambda) ficam em `interface/`, finos: leem entrada, chamam o caso
  de uso via `composition_root`, formatam a saida.

## Fluxo de refatoracao simples (passo a passo)

Quando um modulo cresce ou mistura responsabilidades:

1. Identifique as responsabilidades misturadas (parsing, LLM, persistencia, I/O).
2. Extraia cada uma para sua camada (SRP).
3. Defina uma port (abstracao) para cada dependencia externa (DIP).
4. Mova a implementacao concreta para `infrastructure/` (adapter).
5. Injete as ports no caso de uso via `composition_root` (DIP + OCP).
6. Garanta que os testes do dominio nao precisam de rede/DB (mocks das ports).
7. Rode os testes; a refatoracao deve preservar o comportamento.

## Boas praticas complementares

- Nomes reveladores de intencao (`AnswerQuestion`, `SessionWriter`), nao genericos.
- Funcoes/metodos curtos e coesos; uma tarefa por funcao.
- Dependa de abstracoes nas assinaturas; injete pelo construtor.
- Evite estado global e singletons ocultos; centralize composicao no root.
- Erros de dominio em `domain/exceptions.py`; traduza para HTTP/CLI na `interface/`.
- Nao vaze tipos de biblioteca (ex.: objetos da Agno) para fora da infraestrutura.
- Espelhe a estrutura em `tests/`; teste dominio e casos de uso isoladamente.
- Evite over-engineering: aplique camadas conforme o projeto cresce; um script de
  estudo pequeno pode comecar simples e evoluir para esta estrutura.

## Checklist de arquitetura

- [ ] `domain/` nao importa framework, LLM, DB nem `infrastructure/`.
- [ ] Cada classe/modulo tem uma unica responsabilidade (SRP).
- [ ] Novos providers/DBs entram como novos adapters, sem editar casos de uso (OCP).
- [ ] Toda implementacao respeita o contrato de sua port (LSP).
- [ ] Ports pequenas e focadas; sem metodos nao usados (ISP).
- [ ] Casos de uso recebem abstracoes por injecao; composicao no root (DIP).
- [ ] `tests/` espelha `src/`; dominio testavel sem rede/DB.

## Desenvolvimento produtivo (serverless: Lambda + API Gateway + Layers + SAM)

Como manter a arquitetura em camadas + SOLID quando o codigo roda em AWS Lambda,
exposto por API Gateway, com libs em Layers e deploy via AWS SAM. O objetivo e um
ciclo de dev rapido, com feedback local e deploy reprodutivel.

Para os detalhes de infra (template SAM completo, IAM, segredos, timeouts, padroes
sync/streaming/async), use a skill `agno-lambda-apigateway`. Aqui o foco e ONDE cada
peca mora na arquitetura e COMO trabalhar de forma produtiva.

### Mapa: arquitetura -> serverless

- `interface/http/lambda_handler.py` = o handler da Lambda. Fino: le o evento do
  API Gateway, chama o caso de uso via `composition_root`, formata a resposta HTTP.
- `application/` e `domain/` = codigo da funcao (leve). Nao conhecem AWS nem Agno.
- `infrastructure/` = adapters concretos (Agno, DynamoDB, Secrets Manager) — usam
  boto3/SDKs. Trocaveis sem afetar dominio/casos de uso.
- Libs pesadas (agno, sdk do provider) = Lambda Layer, nao vao no pacote da funcao.
- `template.yaml` (SAM) = a fronteira de infraestrutura; declara funcao, layer,
  API, IAM e recursos (DynamoDB/Secret). Fica na raiz do projeto, fora de `src/`.

Beneficio SOLID: como o handler so traduz HTTP <-> caso de uso, trocar o gatilho
(API Gateway REST, HTTP API, Function URL, SQS worker) e so adicionar outro adapter
em `interface/`, sem tocar em `application/`/`domain/` (OCP + DIP).

### Estrutura de pastas (projeto serverless)

```
<projeto>/
├── template.yaml            # infra SAM (fora de src/)
├── samconfig.toml           # config de deploy por ambiente
├── src/
│   └── <pacote>/            # domain, application, infrastructure, interface
├── layers/
│   └── deps/
│       └── requirements.txt # libs pesadas (agno, provider) -> vira o Layer
├── events/
│   └── invoke.json          # evento de teste (sam local invoke)
└── tests/
```

- O handler declarado no SAM aponta para o modulo em `src/` (ex.:
  `Handler: <pacote>.interface.http.lambda_handler.handler`).
- O `requirements.txt` da funcao fica minimo; as libs pesadas vao no Layer.

### Ciclo de desenvolvimento produtivo

1. Escreva/teste o dominio e os casos de uso PRIMEIRO, sem AWS (pytest puro, ports
   mockadas). E o ciclo mais rapido e onde vive a regra de negocio.
2. Implemente os adapters de `infrastructure/` (Agno, DynamoDB, Secrets).
3. Ligue tudo no `composition_root`; o handler so chama o caso de uso.
4. Rode localmente com SAM antes de subir:

```bash
sam build --use-container          # compila deps do Layer no ambiente da Lambda
sam local invoke ChatFn -e events/invoke.json
sam local start-api                # sobe a API em http://localhost:3000
```

5. Deploy quando o local estiver ok:

```bash
sam deploy --guided                # primeiro deploy (salva samconfig.toml)
sam deploy                          # deploys seguintes
```

### Produtividade com Layers

- Separe o Layer de libs pesadas (agno + provider) do codigo da funcao: assim voce
  altera codigo e faz deploy rapido sem reempacotar as dependencias todo build.
- Reaproveite o mesmo Layer entre varias funcoes/agentes (consistencia de versao).
- Fixe versoes no `layers/deps/requirements.txt` (`agno==x.y.z`) para builds
  reprodutiveis; so refaca o build do Layer quando as deps mudarem.
- `sam build --use-container` garante wheels compativeis com o runtime da Lambda
  (evita "funciona na minha maquina" com libs nativas).

### Boas praticas de dev produtivo serverless

- Handlers finos: nenhuma regra de negocio no handler; so entrada/saida HTTP.
- Instancie agente/adapters no escopo do modulo (reuso entre invocacoes; menor cold
  start) — combina com o `composition_root` chamado uma vez por container.
- Iteracao rapida: teste dominio/casos de uso localmente; use `sam local` so para
  validar a borda (evento, permissoes, formato de resposta).
- Ambientes isolados (dev/staging/prod) via parametros do SAM e stacks separadas;
  nunca compartilhe tabela/segredo entre ambientes.
- Feedback rapido em nuvem: `sam logs -n ChatFn --tail` para acompanhar CloudWatch;
  habilite X-Ray para tracing ponta a ponta.
- CI/CD: `sam build` + `sam deploy` em pipeline; segredos injetados fora do repo.
- Nao acople o dominio ao evento do API Gateway: converta o evento em DTO na
  `interface/` e passe o DTO ao caso de uso (mantem o dominio portavel/testavel).

### Checklist de dev produtivo serverless

- [ ] Handler fino em `interface/http/`; regra de negocio fora dele.
- [ ] Libs pesadas no Layer; `requirements.txt` da funcao minimo.
- [ ] Versoes fixadas no Layer; `sam build --use-container`.
- [ ] Dominio/casos de uso testados sem AWS (ports mockadas).
- [ ] `sam local invoke`/`start-api` validando a borda antes do deploy.
- [ ] Stacks e recursos separados por ambiente (dev/staging/prod).
- [ ] Detalhes de infra (IAM, segredos, timeouts) conforme `agno-lambda-apigateway`.
