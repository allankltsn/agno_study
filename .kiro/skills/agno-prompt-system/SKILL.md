---
name: agno-prompt-system
description: Escrita de prompts de sistema/instrucao para agentes Agno seguindo melhores praticas e adaptando ao modelo alvo (OpenAI GPT, Groq/Llama, Anthropic Claude, Google Gemini, xAI Grok, Moonshot Kimi K3, AWS Bedrock, Ollama locais), que tem especificidades proprias. Os prompts de preferencia sao a fonte de verdade e ficam num bucket S3 (com fallback local). Use ao escrever, revisar ou refatorar instructions/description/system prompt de qualquer agente.
---

# Agno Prompt System

Como escrever os **prompts de instrucao** (system prompt / `description` +
`instructions`) de um agente para que sejam claros, robustos e **adaptados ao
modelo alvo**. Dois eixos:

1. **Melhores praticas gerais** — valem para qualquer modelo.
2. **Especificidades por modelo** — cada familia (GPT, Llama, Claude, Gemini,
   Grok, Kimi, Bedrock, Ollama) responde melhor a um estilo de prompt.

Objetivo: instrucoes que produzem comportamento previsivel, sao testaveis e nao
precisam ser reescritas do zero ao trocar de provider (o repo troca provider por
`MODEL_PROVIDER_ORDER`).

## Onde o prompt vive na arquitetura

- No Agno, o system prompt e composto por `description` (quem o agente e) +
  `instructions` (como ele age) + contexto injetado (`dependencies`/`context`).
- No agente 01, isso e `_DEFAULT_INSTRUCTIONS` (lista de regras) e o
  `_PRODUCT_CONTEXT`, aplicados a **todos** os adapters no `composition_root`.
- A **persona e as regras sao as mesmas** para todos os providers; o que muda por
  modelo e a **formatacao/estilo** do prompt (ver secao por modelo). Mantenha a
  intencao unica e adapte a forma — nao duplique a persona por provider.
- Os **prompts de preferencia** (persona + regras canonicas) sao a fonte de verdade
  e ficam num **bucket S3** versionado, lidos na montagem com **fallback local**
  (ver secao "Fonte dos prompts: bucket S3"). O codigo nunca fica sem prompt.

## Melhores praticas gerais (qualquer modelo)

### Estrutura: persona -> regras -> contexto -> formato de saida

Separe as tres coisas em vez de amontoar tudo num paragrafo:

```python
description = "Voce e um assistente de suporte ao cliente, cordial e objetivo."

instructions = [
    # Regras de comportamento (o "como")
    "Responda em portugues do Brasil, de forma clara e concisa.",
    "Use somente as informacoes da documentacao do produto fornecida no contexto.",
    "Se a resposta nao estiver na documentacao, diga que nao sabe e oriente abrir chamado.",
    # Formato de saida (contrato)
    "Responda em no maximo 4 frases; use lista apenas quando houver passos.",
]
```

### Regras de ouro

- **Especifico > generico.** "Responda em ate 4 frases" vale mais que "seja conciso".
- **Positivo > negativo.** Diga o que fazer ("use apenas o contexto fornecido")
  em vez de so proibir ("nao invente"). Quando proibir, seja concreto.
- **Uma regra por item.** Lista de `instructions` com itens atomicos e mais facil
  de testar e de o modelo seguir do que um paragrafo denso.
- **Defina o desconhecido.** Diga explicitamente o que fazer quando faltar
  informacao (recusar, pedir esclarecimento, abrir chamado) — reduz alucinacao.
- **Contrato de saida explicito.** Se o consumidor espera JSON/estrutura, descreva
  o formato exato e de 1 exemplo. Nao dependa de "formate bonito".
- **Poucos exemplos bem escolhidos** (few-shot) > muitos exemplos ruidosos. Cubra
  o caso normal e 1 caso de borda.
- **Ordem importa.** Instrucoes no inicio e no fim do prompt tem mais peso; ponha
  o mais critico nas pontas.
- **Nao coloque segredos no prompt.** Chaves/credenciais nunca entram em
  `instructions`/`context` (ver skill `agno-secrets`).
- **Versione o prompt.** Trate o system prompt como codigo: mudanca de prompt e
  mudanca de comportamento -> registre no CHANGELOG e teste (ver mais abaixo).

### Anti-padroes

- Prompt gigante tentando cobrir todo caso possivel (dilui o sinal).
- Regras contraditorias ("seja detalhado" + "responda em 1 frase").
- Persona e dados misturados (dados factuais devem ir no contexto, nao na persona).
- Instrucao dependente de detalhe de UM modelo (ex.: contar com um comportamento
  especifico do GPT) sem fallback — quebra ao cair para outro provider.

## Especificidades por modelo

A intencao e a mesma; ajuste **a forma**. Diretrizes praticas por familia:

### OpenAI GPT (ex.: gpt-4o-mini)

- Segue bem instrucoes em linguagem natural e listas numeradas/bullets.
- Suporta bem **contrato de saida estruturada** (JSON) — descreva o schema e, se
  disponivel no adapter, use saida estruturada/`response_format`.
- Papeis (`system`/`user`) sao respeitados: coloque persona e regras no system.
- Menos sensivel a formatacao exotica; prefira prosa clara + bullets.

### Groq / Llama (ex.: llama-3.3-70b-versatile)

- Modelos Llama respondem melhor a **prompts explicitos e estruturados**, com
  seccoes marcadas (ex.: cabecalhos `### Tarefa`, `### Regras`, `### Formato`).
- Reforce o formato de saida **no fim** do prompt (recencia forte).
- Podem precisar de instrucao mais direta para recusar/abster-se; seja literal
  sobre "se nao souber, diga X".
- Latencia baixa no Groq favorece prompts um pouco mais verbosos sem custo de UX.

### Anthropic Claude (se/quando adicionado)

- Prefere **tags XML** para delimitar seccoes (`<contexto>...</contexto>`,
  `<regras>...</regras>`) — melhora aderencia.
- Responde bem a instrucoes longas e a pedir raciocinio passo a passo antes da
  resposta final (quando a tarefa exige).
- Coloque instrucoes no system e o material de referencia em tags no user turn.
- No AWS Bedrock a familia Claude mantem as mesmas preferencias (tags XML); so
  muda o provider de acesso, nao o estilo do prompt.

### Google Gemini (ex.: gemini-2.0-flash)

- Segue bem instrucoes em linguagem natural; aceita prompts longos (janela de
  contexto grande), mas ainda vale manter regras atomicas.
- Forte em **multimodal** — ao usar imagem/audio, descreva no prompt o que fazer
  com cada modalidade explicitamente.
- Reforce o contrato de saida; para JSON, peca o schema e valide na aplicacao.
- Evite depender de formatacao exotica; prosa clara + bullets funcionam bem.

### xAI Grok (ex.: grok-4-fast / grok-3-mini)

- Estilo proximo dos modelos OpenAI-compativeis: instrucoes diretas, bullets,
  contrato de saida explicito.
- Modelos "mini"/reasoning aceitam `reasoning_effort`; quando ativo, deixe a
  tarefa clara e evite microgerenciar o passo a passo no prompt.
- Reforce a abstencao ("se nao souber, diga X") de forma literal.

### Moonshot Kimi (ex.: Kimi K3)

- Kimi **raciocina por padrao**: nao peca "pense passo a passo" no prompt de forma
  redundante — o modelo ja produz `reasoning_content`. Controle o esforco por
  `use_thinking`/`reasoning_effort` (config do provider), nao inflando o prompt.
- Para tarefas simples/rapidas, desligue o thinking (`use_thinking=False`) em vez
  de instruir o modelo a "ser breve".
- **Saida estruturada**: Kimi emite apenas **objeto JSON** no topo, nunca um array.
  Ao exigir listas, peca um objeto com um campo lista (ex.: `{"itens": [...]}`),
  nunca um array raiz. Descreva bem os campos (as descricoes guiam o JSON mode).
- Estilo geral proximo de OpenAI-compativel: instrucoes diretas, bullets, contrato
  de saida explicito; reforce a abstencao ("se nao souber, diga X").

### AWS Bedrock (ex.: us.anthropic.claude-3-5-haiku, meta.llama, mistral)

- Bedrock e um **meio de acesso**, nao uma familia de modelo: o estilo do prompt
  segue a familia do modelo escolhido (Claude -> tags XML; Llama -> seccoes
  marcadas; Mistral -> instrucoes diretas).
- Escreva o prompt para a **familia subjacente**, nao para "Bedrock".
- Confirme o suporte a saida estruturada por modelo antes de exigir JSON estrito.

### Ollama / modelos locais (ex.: llama3.2)

- Modelos menores/quantizados seguem menos instrucoes simultaneas: **reduza o
  numero de regras**, encurte a persona, evite few-shot longo.
- Seja ainda mais explicito no formato de saida e valide/parseie defensivamente
  na aplicacao (o modelo pode desviar do formato).
- Prefira tarefas mais estreitas por chamada; quebre pedidos complexos.

> Regra pratica: escreva o prompt **canonico** (persona + regras + formato) uma vez
> e aplique um leve **ajuste de estilo por familia** no adapter, sem mudar a
> intencao. Nunca mantenha duas personas divergentes por provider.

## Fonte dos prompts: bucket S3 (com fallback local)

Os **prompts de preferencia** (persona + regras canonicas) sao a fonte de verdade
e ficam versionados num **bucket S3**, nao hardcoded no codigo. Assim da para
ajustar o comportamento do agente sem redeploy, e o mesmo prompt vale para local
(LocalStack) e producao (S3 real) — trocando so o endpoint por env.

Convencao de env (padrao env->default da skill `agno-providers`):

```dotenv
PROMPTS_BUCKET=suporte-prompts            # bucket dos prompts de preferencia
PROMPTS_KEY=suporte/system_prompt.md      # objeto (chave) do prompt canonico
AWS_ENDPOINT_URL=http://localhost:4566    # LocalStack no dev; vazio/ausente em prod
```

Modele como **porta + adapter** (consistente com `agno-secrets`), com **fallback
local** quando o S3 nao estiver disponivel — o agente nunca sobe sem prompt:

```python
# infrastructure/prompt/prompt_source.py
from __future__ import annotations

import os
from typing import Optional, Protocol


class PromptSource(Protocol):
    """Fonte do prompt canonico (ex.: bucket S3)."""

    def load(self) -> Optional[str]:
        ...


class S3PromptSource:
    """Le o prompt de preferencia de um objeto no S3.

    Import do boto3 tardio; retorna None em falha para permitir fallback local
    (nunca deixa o agente sem prompt). NUNCA loga o conteudo do prompt.
    """

    def __init__(self, bucket: str, key: str, endpoint_url: Optional[str] = None) -> None:
        self._bucket = bucket
        self._key = key
        self._endpoint = endpoint_url or os.environ.get("AWS_ENDPOINT_URL") or None

    def load(self) -> Optional[str]:
        try:
            import boto3  # import tardio

            client = boto3.client("s3", endpoint_url=self._endpoint)
            obj = client.get_object(Bucket=self._bucket, Key=self._key)
            text = obj["Body"].read().decode("utf-8").strip()
            return text or None
        except Exception:
            # S3 indisponivel/objeto ausente => cai no fallback local (baseline).
            return None
```

O resolver do prompt combina S3 (preferencia) com o baseline local (fail-safe):

```python
# infrastructure/prompt/system_prompt.py (trecho)
def load_system_prompt(source: Optional[PromptSource]) -> str:
    """Retorna o prompt canonico: S3 se disponivel, senao o baseline local.

    O baseline local garante que o agente sempre tenha um prompt valido mesmo
    sem rede (KISS + resiliencia). O S3 e a fonte de PREFERENCIA.
    """
    if source is not None:
        remote = source.load()
        if remote:
            return remote
    return "\n".join([PERSONA, *BASE_RULES])  # fallback local
```

No `composition_root`, construa a fonte S3 a partir das envs e injete-a; o baseline
local abaixo continua sendo a rede de seguranca.

## Padrao sugerido no codigo (prompt canonico + estilo por familia)

Mantenha o prompt canonico central e, se precisar adaptar por familia, faca no
ponto de montagem (adapter), nao no dominio:

```python
# infrastructure/prompt/system_prompt.py
from __future__ import annotations

from typing import List

# Prompt canonico: persona + regras + contrato de saida. Fonte unica de verdade.
PERSONA = "Voce e um assistente de suporte ao cliente, cordial e objetivo."

BASE_RULES: List[str] = [
    "Responda em portugues do Brasil, de forma clara e concisa.",
    "Use somente as informacoes da documentacao do produto fornecida no contexto.",
    "Se a resposta nao estiver na documentacao, diga que nao sabe e oriente abrir chamado.",
    "Responda em no maximo 4 frases; use lista apenas quando houver passos.",
]


def instructions_for(provider: str) -> List[str]:
    """Ajusta apenas o ESTILO do prompt por familia de modelo (mesma intencao).

    - llama/groq/ollama: reforca o formato de saida no fim (recencia).
    - claude: (quando existir) poderia envelopar contexto em tags XML.
    - openai: usa o canonico como esta.
    """
    rules = list(BASE_RULES)
    family = provider.lower()
    # Modelos que se beneficiam de reforco do formato no fim do prompt (recencia).
    if family in {"groq", "ollama", "gemini", "xai", "moonshot", "bedrock"}:
        rules.append("IMPORTANTE: siga estritamente o limite de 4 frases acima.")
    return rules
```

O `composition_root` ja injeta `instructions` por adapter — este helper substitui
a lista fixa `_DEFAULT_INSTRUCTIONS` por uma versao ciente do provider, sem tocar
em `domain/` nem `application/`.

## Testabilidade (prompt e comportamento)

- Trate o prompt como contrato: escreva ao menos um teste de comportamento por
  regra critica (ex.: "quando a pergunta esta fora da doc, a resposta contem a
  orientacao de abrir chamado").
- Fixe `temperature=0` nos testes de comportamento para reduzir variancia.
- Cubra a abstencao (o modelo recusa quando nao sabe) — e a regra mais quebrada.
- Ao trocar de provider, rode os mesmos testes: se um provider precisa de ajuste
  de estilo, o teste mostra onde.

## Regras (checklist)

- Persona (`description`) separada de regras (`instructions`) separada de dados
  (contexto).
- Regras atomicas, positivas e especificas; contrato de saida explicito.
- Comportamento de abstencao definido (o que fazer sem informacao).
- Prompt canonico unico; ajuste por modelo altera **forma**, nunca **intencao**.
- Prompts de preferencia no **bucket S3** (fonte de verdade), com **fallback local**
  para o agente nunca subir sem prompt.
- Sem segredos no prompt.
- Prompt versionado + testado; mudanca de prompt = mudanca de comportamento.

## Relacao com outras skills

- `agno-basic-agents`: onde `description`/`instructions`/`context` sao aplicados no `Agent`.
- `agno-secrets`: nunca colocar chaves/credenciais no prompt; a fonte S3 usa a
  mesma cadeia de credenciais AWS.
- `agno-local-dev-environment`: o bucket S3 dos prompts e criado no bootstrap do
  LocalStack; em dev o `AWS_ENDPOINT_URL` aponta para o LocalStack.
- `agno-project-architecture-solid`: prompt/estilo por modelo vive na infraestrutura
  (adapter/ponto de montagem), nao no dominio.
