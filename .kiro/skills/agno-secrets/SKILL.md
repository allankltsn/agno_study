---
name: agno-secrets
description: Resolucao de configuracao e segredos em agentes Agno com pipe de fallback (env var padrao -> AWS Secrets Manager) e falha rapida (exception) quando uma variavel obrigatoria nao e encontrada em nenhuma fonte. Use ao ler chaves de API, credenciais ou qualquer segredo/config sensivel.
---

# Agno Secrets

Como um agente resolve **configuracao e segredos** (chaves de API, credenciais,
endpoints sensiveis) de forma segura e testavel. A regra central e um **pipe de
fallback** com **falha rapida**:

> 1. procura na variavel de ambiente padrao;
> 2. se ausente, consulta o **AWS Secrets Manager**;
> 3. se continuar ausente **e** a variavel for **obrigatoria**, levanta uma
>    exception que **impede a aplicacao de subir** (fail fast).

Objetivo: nunca subir um agente meio-configurado (que so quebraria no meio de um
atendimento) e nunca hardcodar/logar segredos.

## Onde isso vive na arquitetura

- A resolucao de segredos e **infraestrutura**. Vive em `infrastructure/` e e
  acionada no `composition_root` (ponto de montagem). O `domain/` e o
  `application/` **nunca** leem env vars nem falam com o Secrets Manager (DIP).
- O `config.py` conhece apenas os **nomes** das variaveis (nunca os valores). O
  **valor** de um segredo so e lido em tempo de execucao, no ponto de montagem, e
  **nunca** e guardado em `Settings`, logado ou colocado em mensagens de erro.
- Alinha com o padrao ja existente no repo: `ProviderConfig.api_key_env` guarda o
  *nome* da env (ex.: `OPENAI_API_KEY`), e `_require_api_key` aborta nomeando a
  env ausente sem expor o valor. Esta skill **estende** esse padrao adicionando a
  fonte secundaria (Secrets Manager) antes de abortar.

## O pipe de fallback (contrato)

```
resolve(nome_da_env, *, obrigatorio) ->
    1. valor = os.environ.get(nome_da_env)          # fonte 1: env padrao
       -> se presente e nao-vazio: retorna valor
    2. valor = secrets_manager.get(nome_da_env)     # fonte 2: Secrets Manager
       -> se presente e nao-vazio: retorna valor
    3. se obrigatorio: raise MissingSecretError(nome_da_env)   # fail fast
       senao: retorna None (ou default)
```

Regras do contrato:

- A ordem e sempre **env primeiro, Secrets Manager depois**. Env vence porque
  permite override local/CI e testes sem tocar na nuvem.
- Uma variavel **vazia** conta como ausente (`""` e whitespace nao sao valores).
- A exception e levantada **na montagem** (`composition_root` / `load_settings`),
  antes de servir qualquer requisicao — nunca no meio de um atendimento.
- A mensagem de erro **nomeia a env ausente**, **nunca** o valor de nenhum
  segredo (Req 10.6 / 11.5 do agente 01).

## Fonte de segredos: porta + adapters

Modele a fonte de segredos como uma **porta** (interface), com adapters concretos.
Isso mantem o resolver testavel (sem rede) e permite trocar backend por config.

```python
# infrastructure/secrets/ports.py
from __future__ import annotations

from typing import Optional, Protocol


class SecretsProvider(Protocol):
    """Fonte secundaria de segredos (ex.: AWS Secrets Manager).

    Retorna o valor do segredo pela chave, ou None quando nao existe.
    Implementacoes NUNCA logam o valor retornado.
    """

    def get(self, key: str) -> Optional[str]:
        ...
```

Adapter para AWS Secrets Manager (o repo ja provisiona o segredo
`suporte-sla-api-keys` no LocalStack; o segredo guarda um JSON de chaves):

```python
# infrastructure/secrets/aws_secrets_manager.py
from __future__ import annotations

import json
import os
from typing import Dict, Optional


class AWSSecretsManagerProvider:
    """Le um segredo JSON do AWS Secrets Manager e resolve chaves por nome.

    O segredo e um objeto JSON tipo {"OPENAI_API_KEY": "...", "GROQ_API_KEY": "..."}.
    Faz cache do JSON em memoria (uma leitura por processo) para nao chamar a AWS
    a cada `get`. Import do boto3 e tardio: o modulo permanece importavel sem o SDK.
    """

    # Nome logico do segredo; sobrescrevivel por env, com default do repo.
    SECRET_NAME_ENV = "SECRET_NAME"
    DEFAULT_SECRET_NAME = "suporte-sla-api-keys"

    def __init__(self, secret_name: Optional[str] = None) -> None:
        self._secret_name = (
            secret_name
            or os.environ.get(self.SECRET_NAME_ENV)
            or self.DEFAULT_SECRET_NAME
        )
        self._cache: Optional[Dict[str, str]] = None

    def _load(self) -> Dict[str, str]:
        if self._cache is not None:
            return self._cache
        # Import tardio: sem boto3 instalado, so falha se a fonte for realmente usada.
        import boto3  # type: ignore

        # endpoint_url respeita LocalStack via AWS_ENDPOINT_URL, se definido.
        client = boto3.client(
            "secretsmanager",
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        )
        resp = client.get_secret_value(SecretId=self._secret_name)
        raw = resp.get("SecretString") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Segredo nao-JSON: trate como vazio em vez de vazar o conteudo.
            data = {}
        # Normaliza tudo para str; ignora nao-strings silenciosamente.
        self._cache = {k: str(v) for k, v in data.items() if isinstance(v, str)}
        return self._cache

    def get(self, key: str) -> Optional[str]:
        value = self._load().get(key)
        return value or None  # "" vira None (vazio == ausente)
```

Para testes, um adapter em memoria basta (nenhuma rede):

```python
# infrastructure/secrets/in_memory.py
from __future__ import annotations

from typing import Dict, Optional


class InMemorySecretsProvider:
    def __init__(self, secrets: Optional[Dict[str, str]] = None) -> None:
        self._secrets = dict(secrets or {})

    def get(self, key: str) -> Optional[str]:
        value = self._secrets.get(key)
        return value or None
```

## O resolver (pipe + fail fast)

```python
# infrastructure/secrets/resolver.py
from __future__ import annotations

import os
from typing import Optional

from suporte.infrastructure.secrets.ports import SecretsProvider


class MissingSecretError(RuntimeError):
    """Segredo obrigatorio ausente em TODAS as fontes. Impede a app de subir.

    Nomeia SOMENTE o nome da env ausente — nunca o valor de qualquer segredo.
    """


class SecretResolver:
    """Resolve config/segredos por pipe de fallback: env -> Secrets Manager.

    `secrets_provider` e opcional: sem ele, o pipe usa apenas a env (util em dev
    puro sem AWS). Env sempre vence sobre o Secrets Manager.
    """

    def __init__(self, secrets_provider: Optional[SecretsProvider] = None) -> None:
        self._secrets = secrets_provider

    def resolve(
        self,
        env_name: str,
        *,
        required: bool = True,
        default: Optional[str] = None,
    ) -> Optional[str]:
        # Fonte 1: variavel de ambiente padrao (vence sempre).
        raw = os.environ.get(env_name)
        if raw is not None and raw.strip() != "":
            return raw

        # Fonte 2: Secrets Manager (se configurado).
        if self._secrets is not None:
            from_sm = self._secrets.get(env_name)
            if from_sm is not None and from_sm.strip() != "":
                return from_sm

        # Fonte 3: nada encontrado.
        if required and default is None:
            # Fail fast: nomeia a env ausente, NUNCA o valor de segredo.
            raise MissingSecretError(
                f"Configuracao obrigatoria ausente: '{env_name}' nao foi encontrada "
                f"nem nas variaveis de ambiente nem no Secrets Manager. "
                f"Defina '{env_name}' e tente novamente."
            )
        return default
```

## Ligando no composition_root (montagem)

Injete o resolver no ponto de montagem e use-o **antes** de construir adapters.
Isto substitui o `_require_api_key` (que so olhava a env) por uma verificacao que
tambem consulta o Secrets Manager antes de abortar:

```python
# composition_root.py (trecho)
from suporte.infrastructure.secrets.aws_secrets_manager import AWSSecretsManagerProvider
from suporte.infrastructure.secrets.resolver import SecretResolver

def _build_secret_resolver() -> SecretResolver:
    # Secrets Manager e opcional: liga so quando ha endpoint/uso de AWS.
    provider = AWSSecretsManagerProvider() if _aws_secrets_enabled() else None
    return SecretResolver(secrets_provider=provider)

def _require_api_key(cfg, resolver: SecretResolver) -> None:
    # Levanta MissingSecretError (fail fast) se a chave nao existir em NENHUMA fonte.
    # NUNCA guarda/loga o valor: so confirma presenca. O SDK do provider lera a
    # chave do ambiente; se veio do Secrets Manager, exporte-a no os.environ aqui
    # (uma unica vez, na montagem) para o SDK enxergar.
    value = resolver.resolve(cfg.api_key_env, required=True)
    # setdefault so escreve se a chave nao existir. Se a env existir VAZIA, o
    # resolve ja teria buscado no Secrets Manager; force a atribuicao para o SDK
    # enxergar o valor resolvido em vez do "" pre-existente.
    os.environ[cfg.api_key_env] = value
```

Chame `resolver.resolve(...)` para toda config obrigatoria durante
`build_answer_question()` / `load_settings()`. Se algo faltar, a `MissingSecretError`
sobe **antes** de o agente atender qualquer pergunta.

## Regras (checklist de seguranca)

- **Ordem fixa**: env padrao primeiro, Secrets Manager depois. Env sempre vence.
- **Fail fast**: variavel obrigatoria ausente em todas as fontes => exception na
  montagem, aplicacao nao sobe.
- **Vazio == ausente**: `""`/whitespace nao contam como valor.
- **Nunca logar valores** de segredo. Erros e logs citam apenas o **nome** da env.
- **Nunca hardcodar** chaves; o codigo conhece so o nome da env
  (`ProviderConfig.api_key_env`).
- **Import tardio do boto3**: o modulo de secrets permanece importavel/testavel
  sem o SDK da AWS.
- **Testavel sem rede**: use `InMemorySecretsProvider` nos testes do resolver
  (cobrir os 3 ramos: env presente, so no SM, ausente em ambos -> raise).
- **Cache**: o adapter do Secrets Manager le o segredo uma vez por processo (uma
  chamada de rede na montagem, nao por requisicao).

## Relacao com o repo (agente 01-suporte-sla-fallback)

- LocalStack ja provisiona o segredo `suporte-sla-api-keys` (JSON com
  `OPENAI_API_KEY`/`GROQ_API_KEY`) em `localstack/init/01-bootstrap.sh` (Req 15.7),
  de forma idempotente (Req 15.8).
- O `AWSSecretsManagerProvider` acima le exatamente esse segredo e resolve as
  chaves por nome, encaixando no `MODEL_PROVIDER_ORDER` sem tocar em `domain/`.
- Providers locais (Ollama) nao usam chave: nao passam pelo `resolve(required=True)`
  de chave — seguem lendo `OLLAMA_HOST` da env.
