---
name: agno-guardrails
description: Guardrails obrigatorios para todo agente Agno - stack minima (PII + Prompt Injection + OpenAI Moderation), um guardrail customizado de escopo (bloqueia mensagens fora do dominio do agente) e, opcionalmente, AWS Bedrock Guardrails habilitado por variavel de ambiente. Use ao criar ou revisar qualquer agente, garantindo que ele nunca rode sem guardrail.
---

# Agno Guardrails

Regra inegociavel: **todo agente sempre tem pelo menos um guardrail configurado.**
Um agente sem `pre_hooks` de guardrail e considerado incompleto e nao deve subir.

> Nota de contexto: esta skill referencia uma conversa do Kiro web
> (`0935f27c-4285-4b67-89c6-152ae6333be2`) que **nao esta acessivel** a partir
> deste workspace. O conteudo abaixo segue o objetivo declarado e as melhores
> praticas oficiais da Agno; se aquela conversa definiu decisoes especificas
> (limiares, categorias, mensagens), ajuste esta skill ao cola-las aqui.

## O que sao (na Agno)

Guardrails sao **pre-hooks**: executam depois do carregamento da sessao e da
resolucao de dependencias, **antes** de montar o contexto do modelo. Eles validam
a entrada do usuario e **bloqueiam** a execucao levantando `InputCheckError`.

- `run()`/`arun()` capturam o erro e retornam `RunStatus.error`.
- Runs em streaming emitem `RunError`/`TeamRunError`.
- Passe os guardrails via `pre_hooks=[...]` no `Agent` (ou `Team`).

Limitacao importante (da doc): os guardrails checam **o input do run**, nao
garantem seguranca total nem inspecionam automaticamente historico, dependencias,
documentos recuperados (RAG) ou midia. Escolha os checks conforme a politica da
sua aplicacao e avalie falsos positivos/negativos.

## Stack minima recomendada (obrigatoria)

Alem do OpenAI Moderation, o baseline de todo agente deve incluir:

1. **PII Detection** — `PIIDetectionGuardrail` (evita vazar/aceitar PII).
2. **Prompt Injection** — `PromptInjectionGuardrail` (jailbreak / injecao).
3. **OpenAI Moderation** — `OpenAIModerationGuardrail` (politica de conteudo).
4. **Escopo (customizado)** — bloqueia mensagens fora do dominio do agente
   (ver secao "Guardrail de escopo").

```python
from agno.agent import Agent
from agno.guardrails import (
    PIIDetectionGuardrail,
    PromptInjectionGuardrail,
    OpenAIModerationGuardrail,
)

agent = Agent(
    name="Suporte",
    model=...,  # ver skill agno-providers
    pre_hooks=[
        # Ordem: baratos/deterministicos primeiro, provider externo por ultimo.
        ScopeGuardrail(),             # customizado (abaixo) — heuristica local
        PromptInjectionGuardrail(),   # match de frases, local
        PIIDetectionGuardrail(),      # deteccao de PII, local
        OpenAIModerationGuardrail(),  # chama a API da OpenAI — por ultimo
    ],
)
```

Ordem sugerida: checks **baratos e deterministicos primeiro** (escopo, injecao,
PII), depois os que chamam provider externo (moderation) — falha cedo, gasta
menos. Esta e a mesma ordem usada em `build_guardrails()` mais abaixo.

> `OpenAIModerationGuardrail` chama a API da OpenAI (exige `OPENAI_API_KEY`) e pode
> submeter imagens fornecidas. Documente isso no README do agente.

## Guardrail de escopo (customizado, obrigatorio)

Objetivo: recusar mensagens que **nao pertencem ao escopo** do agente (ex.: um
agente de suporte que recebe "escreva um poema" ou "qual a capital da Franca").
Estenda `BaseGuardrail` e implemente `check` (sync) **e** `async_check` (async):
a Agno escolhe a versao conforme `.run()` ou `.arun()`.

```python
# infrastructure/guardrails/scope_guardrail.py
from __future__ import annotations

import re
from typing import List

from agno.exceptions import CheckTrigger, InputCheckError
from agno.guardrails import BaseGuardrail
from agno.run.agent import RunInput


class ScopeGuardrail(BaseGuardrail):
    """Bloqueia entradas fora do escopo do agente.

    Estrategia em camadas (barata -> cara):
    1. Allowlist/denylist deterministica (palavras-chave do dominio).
    2. (Opcional) classificador leve/LLM-as-judge quando a heuristica for
       insuficiente — so se o custo/latencia forem aceitaveis.

    A mensagem de erro NAO expoe conteudo sensivel nem detalhes internos.
    """

    def __init__(self, in_scope_keywords: List[str] | None = None) -> None:
        # Palavras-chave do dominio; ajuste por agente (ex.: produto, conta, fatura).
        # ATENCAO: allowlist crua bloqueia saudacoes ("ola", "bom dia") como
        # fora-de-escopo (falso positivo). Em producao, trate saudacoes/curtesias
        # e prefira um classificador de intencao; isto e um baseline de estudo.
        self._keywords = [k.lower() for k in (in_scope_keywords or [
            "conta", "senha", "login", "fatura", "pagamento", "produto",
            "pedido", "assinatura", "suporte", "atendimento",
        ])]

    def _is_in_scope(self, text: str) -> bool:
        low = text.lower()
        return any(re.search(rf"\b{re.escape(k)}\b", low) for k in self._keywords)

    def _validate(self, run_input: RunInput) -> None:
        content = run_input.input_content
        if isinstance(content, str) and content.strip():
            if not self._is_in_scope(content):
                raise InputCheckError(
                    "Sua mensagem parece estar fora do escopo deste assistente. "
                    "Posso ajudar com duvidas relacionadas ao produto e a sua conta.",
                    check_trigger=CheckTrigger.INPUT_NOT_ALLOWED,
                )

    def check(self, run_input: RunInput) -> None:
        self._validate(run_input)

    async def async_check(self, run_input: RunInput) -> None:
        self._validate(run_input)
```

Boas praticas do guardrail de escopo:

- **Reforce, nao substitua, o prompt.** O escopo tambem deve estar nas
  `instructions` (skill `agno-prompt-system`); o guardrail e a barreira dura.
- **Prefira allowlist a denylist** quando o dominio for bem definido — mais dificil
  de burlar do que listar tudo que e proibido.
- **Falso positivo tem custo de UX.** Se a heuristica for agressiva, prefira uma
  mensagem de recusa educada e mensuravel; monitore a taxa de bloqueio.
- **Mensagem de recusa clara e sem vazar interno.** Diga o que o agente faz, nao
  por que a regra existe.
- Heuristica de palavra-chave e um baseline de estudo; para producao considere um
  classificador de intencao/topico ou LLM-as-judge, medindo falsos pos/neg.

## AWS Bedrock Guardrails (habilitado por env)

Quando `BEDROCK_GUARDRAIL_ENABLED` for verdadeiro, adicione um guardrail que chama
o **AWS Bedrock Guardrails** (`ApplyGuardrail`) via boto3, convertendo bloqueio em
`InputCheckError`. Fica **desligado por padrao** (env ausente => nao entra no pipe).

Envs (todas com default; ver skill `agno-providers` para o padrao env->default):

```dotenv
BEDROCK_GUARDRAIL_ENABLED=false
BEDROCK_GUARDRAIL_ID=
BEDROCK_GUARDRAIL_VERSION=DRAFT
AWS_REGION=us-east-1
```

```python
# infrastructure/guardrails/bedrock_guardrail.py
from __future__ import annotations

import os

from agno.exceptions import CheckTrigger, InputCheckError
from agno.guardrails import BaseGuardrail
from agno.run.agent import RunInput


class BedrockGuardrail(BaseGuardrail):
    """Aplica um AWS Bedrock Guardrail ao input (ApplyGuardrail).

    Instanciar SOMENTE quando habilitado por env. Import do boto3 e tardio para
    manter o modulo importavel sem o SDK da AWS.
    """

    def __init__(self, guardrail_id: str, version: str = "DRAFT",
                 region: str | None = None) -> None:
        if not guardrail_id:
            # Fail fast: habilitado sem ID configurado e erro de configuracao.
            raise ValueError(
                "BEDROCK_GUARDRAIL_ENABLED=true exige BEDROCK_GUARDRAIL_ID definido."
            )
        self._guardrail_id = guardrail_id
        self._version = version
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")

    def _apply(self, text: str) -> None:
        import boto3  # import tardio

        client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        )
        resp = client.apply_guardrail(
            guardrailIdentifier=self._guardrail_id,
            guardrailVersion=self._version,
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            raise InputCheckError(
                "Sua mensagem foi bloqueada pelas politicas de seguranca.",
                check_trigger=CheckTrigger.INPUT_NOT_ALLOWED,
            )

    def check(self, run_input: RunInput) -> None:
        content = run_input.input_content
        if isinstance(content, str) and content.strip():
            self._apply(content)

    async def async_check(self, run_input: RunInput) -> None:
        # boto3 e sync; para arun, rode em thread (ex.: anyio.to_thread) se a
        # latencia importar. Aqui, chamada direta para simplicidade de estudo.
        self.check(run_input)
```

> Confirme os nomes/estrutura de `apply_guardrail` na doc do boto3/Bedrock da sua
> versao antes de exigir campos especificos — a resposta pode variar por versao.

## Montagem no composition_root (guardrail sempre presente)

Monte a lista de guardrails num unico lugar, garantindo o baseline + escopo, e
adicionando o Bedrock so quando habilitado por env:

```python
def build_guardrails() -> list:
    from agno.guardrails import (
        PIIDetectionGuardrail,
        PromptInjectionGuardrail,
        OpenAIModerationGuardrail,
    )
    from suporte.infrastructure.guardrails.scope_guardrail import ScopeGuardrail

    guardrails = [
        ScopeGuardrail(),            # barato/deterministico primeiro
        PromptInjectionGuardrail(),
        PIIDetectionGuardrail(),
        OpenAIModerationGuardrail(),  # chama provider externo por ultimo
    ]

    if _get_bool("BEDROCK_GUARDRAIL_ENABLED", False):
        from suporte.infrastructure.guardrails.bedrock_guardrail import BedrockGuardrail
        guardrails.append(
            BedrockGuardrail(
                guardrail_id=_get_str("BEDROCK_GUARDRAIL_ID", ""),
                version=_get_str("BEDROCK_GUARDRAIL_VERSION", "DRAFT"),
            )
        )

    # Invariante do repo: a lista NUNCA pode ser vazia.
    if not guardrails:
        raise RuntimeError("Nenhum guardrail configurado; todo agente exige >= 1.")
    return guardrails

# ... ao montar o Agent:
# Agent(model=..., pre_hooks=build_guardrails(), ...)
```

## Tratamento do bloqueio (na fronteira)

O caso de uso/interface deve tratar `RunStatus.error` como recusa e responder de
forma amigavel, sem vazar o motivo interno:

```python
resp = agent.run(mensagem_do_usuario)
if resp.status == RunStatus.error:
    # Logue o trigger para observabilidade; devolva mensagem neutra ao usuario.
    return "Nao consigo ajudar com isso. Posso ajudar com duvidas do produto/conta?"
return resp.content
```

## Regras (checklist)

- **Todo agente tem >= 1 guardrail**; a montagem falha se a lista ficar vazia.
- Baseline: PII + Prompt Injection + OpenAI Moderation + **escopo customizado**.
- Guardrail de escopo bloqueia fora-de-dominio; reforca (nao substitui) o prompt.
- Ordem: deterministicos/baratos primeiro, provider externo por ultimo.
- Bedrock Guardrails **so** quando `BEDROCK_GUARDRAIL_ENABLED=true`; fail fast se
  habilitado sem `BEDROCK_GUARDRAIL_ID`.
- Custom guardrail implementa `check` **e** `async_check`, levanta `InputCheckError`
  com `CheckTrigger.INPUT_NOT_ALLOWED`.
- Import de boto3 tardio; parametros por env com default (skill `agno-providers`).
- Mensagens de recusa neutras (nao vazam interno); logue o trigger para observabilidade.
- Documente no README que o Moderation chama a OpenAI e pode enviar imagens.

## Relacao com outras skills

- `agno-prompt-system`: escopo/persona tambem no prompt; guardrail e a barreira dura.
- `agno-providers`: leitura env->default dos parametros (inclui flags do Bedrock).
- `agno-secrets`: chaves/credenciais (OpenAI, AWS) nunca hardcoded/logadas.
- `agno-observability`: logue bloqueios (trigger, contagem) sem expor conteudo sensivel.

Fontes: doc oficial da Agno v2 ([Guardrails overview](https://docs.agno.com/guardrails/overview),
[Pre/Post-hooks](https://docs.agno.com/hooks/overview)). Conteudo parafraseado para
conformidade com restricoes de licenciamento.
