---
name: agno-providers
description: Adicionar novos providers de modelo a um agente Agno (Google Gemini, xAI Grok, Moonshot Kimi K3, AWS Bedrock, alem de OpenAI/Groq/Ollama) seguindo melhores praticas. O modelo de cada agente e SEMPRE definido por variavel de ambiente, e todo parametro de instanciamento do provider e configuravel por env com um default pre-definido quando ausente. Use ao criar, revisar ou estender adapters/providers de LLM.
---

# Agno Providers

Como adicionar e configurar **providers de modelo** de forma consistente. Duas
regras inegociaveis, alem das melhores praticas do Agno:

1. **Modelo por env, sempre.** O `id` do modelo de cada agente vem de uma variavel
   de ambiente, nunca hardcoded.
2. **Todo parametro de instanciamento e configuravel por env, com default.** Cada
   parametro do construtor do provider (temperature, max_tokens, top_p, timeout,
   region, host, ...) e lido de env; se a env estiver ausente/vazia, aplica-se um
   **default pre-definido**. Nada de valor magico so no codigo sem fallback.

Isso mantem o repo alinhado ao padrao atual: `config.py` conhece so **nomes** de
env (nunca valores/segredos), o `composition_root` instancia os adapters, e trocar
provider e so mudar `MODEL_PROVIDER_ORDER`.

## Principio de leitura de parametros (env -> default)

Reuse helpers de leitura tipada (o repo ja tem `_get_float`/`_get_int` em
`config.py`; adicione `_get_str`/`_get_bool` no mesmo estilo). Contrato:

```
param = env(NOME_DA_ENV) se presente e nao-vazio, senao DEFAULT_PRE_DEFINIDO
```

- Vazio/whitespace conta como ausente (cai no default).
- Valor invalido (ex.: texto onde se espera float) => `ValueError` nomeando a env.
- Segredos (chaves/credenciais) seguem a skill `agno-secrets` (pipe de fallback
  env -> Secrets Manager -> fail fast). Aqui tratamos de **parametros**, nao de
  segredos.

Exemplo de helpers (mesmo estilo do `config.py` atual):

```python
def _get_str(env_name: str, default: str) -> str:
    raw = os.getenv(env_name)
    return raw if raw is not None and raw.strip() != "" else default


def _get_bool(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_opt_float(env_name: str) -> Optional[float]:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} deve ser um numero; valor invalido: {raw!r}") from exc
```

## Padrao de parametros por provider (`ModelParams`)

Em vez de espalhar `os.getenv` pelos adapters, centralize a leitura num value
object por provider. Convencao de nomes de env: `{<PROVIDER>}_{PARAM}`.

```python
# infrastructure/llm/params.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Defaults pre-definidos (usados quando a env correspondente esta ausente/vazia).
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TOP_P = 1.0


@dataclass(frozen=True)
class ModelParams:
    """Parametros comuns de sampling, todos vindos de env com default."""

    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    top_p: float = DEFAULT_TOP_P


def load_model_params(prefix: str) -> ModelParams:
    """Le os parametros de sampling de env usando o prefixo do provider.

    Ex.: prefix="OPENAI" le OPENAI_TEMPERATURE, OPENAI_MAX_TOKENS, OPENAI_TOP_P.
    Qualquer env ausente cai no default pre-definido.
    """
    return ModelParams(
        temperature=_get_float(f"{prefix}_TEMPERATURE", DEFAULT_TEMPERATURE),
        max_tokens=_get_int(f"{prefix}_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        top_p=_get_float(f"{prefix}_TOP_P", DEFAULT_TOP_P),
    )
```

O **`id` do modelo** tambem vem de env, por provider (com default sensato):

```python
# env: OPENAI_MODEL_ID, GROQ_MODEL_ID, GEMINI_MODEL_ID, XAI_MODEL_ID, MOONSHOT_MODEL_ID, BEDROCK_MODEL_ID, ...
model_id = _get_str("GEMINI_MODEL_ID", "gemini-2.0-flash")
```

> No repo, `ProviderConfig.model_id` ja existe; a evolucao e passar a preenche-lo
> a partir de `{PROVIDER}_MODEL_ID` em `default_provider_registry()`/`load_settings`,
> em vez de literais fixos.

## Como adicionar um novo provider (checklist)

1. Adicione o provider ao **registro** (`default_provider_registry`) com seu
   `api_key_env` (nome da env da chave) e `model_id` lido de `{PROVIDER}_MODEL_ID`.
2. Crie o **adapter** em `infrastructure/llm/<provider>_adapter.py`, implementando
   a `LLMPort`, com **import tardio** do Agno (mantem o modulo importavel sem SDK).
3. Leia `id` e `ModelParams` de env dentro do adapter (env -> default).
4. Ligue o `name` no `build_adapter` do `composition_root`.
5. Verifique a chave/credencial exigida via a skill `agno-secrets` (fail fast).
6. Documente as envs novas em `.env.example` (sem valores).
7. Escreva o prompt/estilo pela **familia** do modelo (skill `agno-prompt-system`).

## Providers novos (import + envs)

Imports verificados na doc da Agno v2 ([model index](https://docs.agno.com/models/providers/model-index)).
Conteudo resumido/parafraseado para conformidade de licenciamento.

### Google Gemini

- Import: `from agno.models.google import Gemini`
- Chave: `GOOGLE_API_KEY` (segredo — via `agno-secrets`).
- Modelo por env: `GEMINI_MODEL_ID` (default sugerido: `gemini-2.0-flash`).

```python
# infrastructure/llm/gemini_adapter.py (esqueleto)
def _build_gemini_model(model_id: str, params: ModelParams):
    from agno.models.google import Gemini  # import tardio
    return Gemini(
        id=model_id,
        temperature=params.temperature,
        max_output_tokens=params.max_tokens,
        top_p=params.top_p,
    )
```

### xAI Grok

- Import: `from agno.models.xai import xAI`
- Chave: `XAI_API_KEY`.
- Modelo por env: `XAI_MODEL_ID` (default sugerido: `grok-3-mini`). Parametro extra
  opcional: `XAI_REASONING_EFFORT` (so aplique se presente).

```python
def _build_xai_model(model_id: str, params: ModelParams):
    from agno.models.xai import xAI  # import tardio
    kwargs = dict(id=model_id, temperature=params.temperature, top_p=params.top_p)
    effort = _get_str("XAI_REASONING_EFFORT", "")  # "" => nao passa
    if effort:
        kwargs["reasoning_effort"] = effort
    return xAI(**kwargs)
```

### Moonshot Kimi (ex.: Kimi K3)

- Import: `from agno.models.moonshot import MoonShot`
- Chave: `MOONSHOT_API_KEY`.
- Modelo por env: `MOONSHOT_MODEL_ID` (default sugerido: `kimi-k3`).
- Kimi **raciocina por padrao** (retorna `reasoning_content`). Controle via
  `use_thinking` e/ou `reasoning_effort` — exponha ambos por env (opcionais).

```python
def _build_moonshot_model(model_id: str, params: ModelParams):
    from agno.models.moonshot import MoonShot  # import tardio
    kwargs = dict(id=model_id, temperature=params.temperature, top_p=params.top_p)
    effort = _get_str("MOONSHOT_REASONING_EFFORT", "")  # "" => nao passa
    if effort:
        kwargs["reasoning_effort"] = effort
    # use_thinking: default None (usa o comportamento do modelo). So passa se setado.
    thinking = _get_str("MOONSHOT_USE_THINKING", "")
    if thinking:
        kwargs["use_thinking"] = thinking.strip().lower() in {"1", "true", "yes", "on"}
    return MoonShot(**kwargs)
```

### AWS Bedrock

- Import: `from agno.models.aws import AwsBedrock`
- **Nao usa `*_API_KEY`**: autentica pela cadeia de credenciais AWS
  (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` ou role/profile)
  e regiao (`AWS_REGION`/`AWS_DEFAULT_REGION`).
- Modelo por env: `BEDROCK_MODEL_ID` (ex.: `us.anthropic.claude-3-5-haiku-20241022-v1:0`).
- O `id` do Bedrock e um **id de modelo AWS**, nao um nome de familia.

```python
def _build_bedrock_model(model_id: str, params: ModelParams):
    from agno.models.aws import AwsBedrock  # import tardio
    region = _get_str("AWS_REGION", _get_str("AWS_DEFAULT_REGION", "us-east-1"))
    return AwsBedrock(
        id=model_id,
        region=region,
        temperature=params.temperature,
        max_tokens=params.max_tokens,
    )
```

> Nota: nomes/assinaturas dos parametros podem variar por versao da Agno e por
> modelo do Bedrock. Confirme na doc do provider antes de exigir um parametro
> especifico; nem todo modelo aceita `top_p`/`max_tokens` da mesma forma.

## Exemplo de `.env.example` (novas envs)

```dotenv
# Ordem de fallback (inclua os novos providers conforme o uso)
MODEL_PROVIDER_ORDER=openai,groq

# Modelo por provider (SEMPRE por env; default aplicado se ausente)
OPENAI_MODEL_ID=gpt-4o-mini
GROQ_MODEL_ID=llama-3.3-70b-versatile
GEMINI_MODEL_ID=gemini-2.0-flash
XAI_MODEL_ID=grok-3-mini
MOONSHOT_MODEL_ID=kimi-k3
BEDROCK_MODEL_ID=us.anthropic.claude-3-5-haiku-20241022-v1:0

# Parametros de sampling por provider (default aplicado se ausente)
OPENAI_TEMPERATURE=0.7
OPENAI_MAX_TOKENS=1024
OPENAI_TOP_P=1.0
# (repita o padrao {PROVIDER}_TEMPERATURE / _MAX_TOKENS / _TOP_P para cada provider)

# Chaves/credenciais (segredos — ver skill agno-secrets)
GOOGLE_API_KEY=
XAI_API_KEY=
MOONSHOT_API_KEY=
# AWS Bedrock: credenciais via cadeia AWS
AWS_REGION=us-east-1
```

## Regras (checklist)

- **Modelo sempre por env** (`{PROVIDER}_MODEL_ID`), com default pre-definido.
- **Todo parametro do construtor por env** (`{PROVIDER}_{PARAM}`), com default.
- Vazio == ausente => cai no default; valor invalido => `ValueError` nomeando a env.
- Import do Agno **tardio** (dentro do adapter); modulo importavel sem o SDK.
- Adapter implementa `LLMPort`; concreto conhecido so no `composition_root` (DIP).
- Chaves/credenciais via `agno-secrets` (nunca hardcoded, nunca logadas).
- Bedrock autentica pela cadeia AWS + regiao; nao usa `*_API_KEY`.
- Confirme parametros suportados por provider/versao antes de exigi-los.
- Documente cada env nova em `.env.example` (sem valores reais).

## Relacao com outras skills

- `agno-secrets`: resolucao de chaves/credenciais (env -> Secrets Manager -> fail fast).
- `agno-prompt-system`: estilo de prompt por familia de modelo (Gemini, Grok, Bedrock/Claude).
- `agno-project-architecture-solid`: adapters na infraestrutura, DIP, ports.
- `agno-basic-agents`: parametros de sampling (temperature/top_p/max_tokens) no modelo.

Fontes: doc oficial da Agno v2 (agno.com/models). Conteudo parafraseado para
conformidade com restricoes de licenciamento.
