"""Log estruturado (JSON) por requisição — observabilidade e custo.

Este módulo concentra a emissão do **log estruturado por requisição** exigido
pelos requisitos de observabilidade (Req 9) e de custo/tokens (Req 8). A ideia é
simples e poderosa: para cada requisição atendida, emitir **exatamente uma linha
de JSON** contendo os metadados que permitem responder, depois, perguntas
operacionais — qual provider respondeu, qual a latência, quantos tokens foram
gastos, se houve fallback e se o resultado foi sucesso ou erro.

Por que JSON de uma linha por requisição:
- **Análise barata**: uma linha por requisição é trivial de agregar (p95 de
  latência, média de tokens, taxa de fallback) com ferramentas de log padrão,
  sem parser especial.
- **Correlação**: o mesmo formato é reaproveitado pela camada de tracing
  (`configure_agno_logging`), de modo que log e trace conversam (Req 16.3).

Privacidade e segredos (Req 9.3, 9.4, 10.5):
- A **pergunta em claro nunca é logada**. Ela aparece somente como
  `question_hash`, um resumo determinístico (SHA-256) produzido por
  `hash_question`. Isso evita vazar PII no log e, ao mesmo tempo, permite
  agrupar/contar requisições idênticas pelo hash.
- **Nenhuma chave de API** transita por este módulo: a interface recebe apenas
  metadados já sanitizados (nome do provider, tokens, latência). Não há campo,
  parâmetro ou caminho por onde uma chave possa entrar no registro.

Design da interface (alinhado ao `design.md`):
- `log_request(...)` existe como **função de módulo** (fácil de chamar e testar).
- `StructuredLogger` é uma classe fina que expõe o método `log_request(...)` com
  a mesma assinatura, servindo como a interface `logger` que o `FallbackLLM`
  espera (o núcleo de resiliência chama `logger.log_request(...)`). Injetar uma
  instância desacopla o `FallbackLLM` de *como* o log é emitido (DIP) e facilita
  substituí-lo por um duplo em memória nos testes.

Sem framework: este módulo é Python puro (usa apenas `hashlib`, `json`, `logging`
e `sys`). Nenhum import de Agno/OpenAI/Groq — a Agno fica confinada aos adapters.
"""

import hashlib
import json
import logging
import sys
from typing import Iterable, Optional

__all__ = ["hash_question", "log_request", "StructuredLogger"]

# Resultados válidos de uma requisição (contrato do campo `result`).
_VALID_RESULTS = ("ok", "error")

# Nome do logger padrão usado quando nenhum `logging.Logger` é injetado. Usamos
# o mecanismo padrão da stdlib para respeitar handlers/configuração do processo.
_DEFAULT_LOGGER_NAME = "suporte.request"


def hash_question(question: str) -> str:
    """Produz um resumo determinístico (SHA-256) da pergunta.

    Este helper existe para que a pergunta **nunca** apareça em claro no log
    (Req 9.3/10.5), preservando a privacidade/PII, mas ainda permitindo:
    - agrupar e contar requisições idênticas (mesma pergunta → mesmo hash);
    - correlacionar log e trace pelo mesmo identificador de conteúdo.

    É **determinístico**: a mesma string de entrada gera sempre o mesmo hash,
    dentro e entre processos (ao contrário de `hash()` embutido do Python, que é
    randomizado por processo). A entrada é normalizada para bytes UTF-8 antes de
    calcular o resumo.

    Args:
        question: O texto da pergunta do usuário.

    Returns:
        O digest hexadecimal (64 caracteres) do SHA-256 da pergunta.
    """
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def log_request(
    *,
    question_hash: str,
    provider: str,
    latency_ms: float,
    prompt_tokens: int,
    completion_tokens: int,
    used_fallback: bool,
    attempts: Iterable[str],
    result: str,
    request_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Emite **exatamente um** registro JSON estruturado para a requisição.

    Todos os argumentos são *keyword-only* para tornar cada campo explícito no
    ponto de chamada (evita trocar `prompt_tokens` com `completion_tokens` por
    posição) e para deixar claro que a pergunta só entra como `question_hash`.

    O campo `total_tokens` é **derivado** de `prompt_tokens + completion_tokens`
    (Req 8.3), garantindo consistência: quem loga informa apenas as parcelas.

    Quando `request_id` é informado, ele é gravado como campo do registro para
    correlacionar log e trace: é o **mesmo** valor anexado ao span OTel sob
    `app.request_id` (Req 16.4). O `request_id` é um identificador opaco (sem
    PII) — a pergunta continua entrando somente como `question_hash` (Req 16.5).

    Args:
        question_hash: Resumo determinístico da pergunta (ver `hash_question`).
            Nunca a pergunta em claro.
        provider: Nome do provider que respondeu (ex.: ``"openai"``) ou o
            provider cuja tentativa se esgotou, no caso de erro.
        latency_ms: Latência observada em milissegundos (>= 0). Em eventos de
            erro/fallback pode ser ``0``.
        prompt_tokens: Tokens de entrada (>= 0; zero quando omitidos pelo provider).
        completion_tokens: Tokens de saída (>= 0; zero quando omitidos).
        used_fallback: ``True`` quando a resposta veio de um provider que não é o
            primário (a partir do 2º da ordem).
        attempts: Providers tentados, na ordem (lista de nomes). É serializado
            como lista JSON.
        result: Resultado da requisição, ``"ok"`` ou ``"error"``.
        request_id: Identificador único da requisição (ver
            `tracing.new_request_id`), gravado como campo para correlacionar com o
            atributo `app.request_id` do span (Req 16.4). Quando ``None``, o campo
            é omitido — preservando a compatibilidade de chamadas que não o
            propagam.
        logger: `logging.Logger` opcional onde emitir. Quando ``None``, usa um
            logger padrão configurado para escrever uma linha em ``stdout``.

    Raises:
        ValueError: Se `result` não for ``"ok"`` nem ``"error"``.
    """
    # Contrato explícito do campo `result`: falha cedo se vier algo inesperado,
    # em vez de gravar um log silenciosamente inválido.
    if result not in _VALID_RESULTS:
        raise ValueError(
            "result deve ser 'ok' ou 'error', recebido: {!r}".format(result)
        )

    # `attempts` é normalizado para uma lista concreta: aceita qualquer iterável
    # na entrada, mas o JSON precisa de uma lista serializável e estável.
    attempts_list = list(attempts)

    # Monta o registro. `total_tokens` é derivado aqui — nunca recebido pronto —
    # para não haver como ficar inconsistente com as parcelas (Req 8.3).
    # Observação de segurança: não há chave de API neste dicionário; a pergunta
    # aparece apenas como `question_hash` (Req 9.3, 9.4, 10.5).
    record = {
        "question_hash": question_hash,
        "provider": provider,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "used_fallback": used_fallback,
        "attempts": attempts_list,
        "result": result,
    }

    # `request_id` só entra quando propagado pelo fluxo da requisição. É o mesmo
    # valor anexado ao span como `app.request_id` (Req 16.4). Continua sem PII: a
    # pergunta permanece apenas como `question_hash` (Req 16.5).
    if request_id is not None:
        record["request_id"] = request_id

    # `separators` compacta o JSON em uma única linha (sem espaços supérfluos);
    # `sort_keys` deixa a saída estável para facilitar testes e leitura.
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    target = logger if logger is not None else _get_default_logger()
    # Um único `info` → exatamente uma linha de log por requisição (Req 9.1).
    target.info(line)


def _get_default_logger() -> logging.Logger:
    """Devolve (criando na primeira vez) o logger padrão do módulo.

    Configura um único `StreamHandler` para ``stdout`` que escreve apenas a
    mensagem (a própria linha JSON), sem prefixos de nível/tempo que quebrariam
    o parsing de "uma linha JSON por requisição". É idempotente: só adiciona o
    handler se ainda não houver um, evitando registros duplicados.
    """
    logger = logging.getLogger(_DEFAULT_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Não propaga para o root para evitar formatação/handlers herdados que
        # poderiam duplicar ou "enfeitar" a linha JSON.
        logger.propagate = False
    return logger


class StructuredLogger:
    """Interface fina de log estruturado esperada pelo `FallbackLLM`.

    O núcleo de resiliência (`FallbackLLM`) registra cada requisição chamando
    ``logger.log_request(...)``. Esta classe é essa dependência injetável: ela
    apenas delega para a função de módulo `log_request`, usando um
    `logging.Logger` fixado na construção. Injetá-la (em vez de chamar a função
    global) desacopla o `FallbackLLM` do destino do log e permite trocá-la por um
    duplo em memória nos testes (DIP).

    Attributes:
        _logger: O `logging.Logger` de destino. Quando ``None`` na construção,
            usa o logger padrão do módulo (uma linha JSON em ``stdout``).
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """Cria o logger estruturado.

        Args:
            logger: `logging.Logger` de destino opcional. Se ``None``, será usado
                o logger padrão do módulo na hora de emitir.
        """
        self._logger = logger

    def log_request(
        self,
        *,
        question_hash: str,
        provider: str,
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        used_fallback: bool,
        attempts: Iterable[str],
        result: str,
        request_id: Optional[str] = None,
    ) -> None:
        """Emite um registro estruturado delegando para `log_request`.

        Assinatura idêntica (keyword-only) à função de módulo, exceto por não
        expor `logger` — o destino é o fixado na construção. Ver `log_request`
        para a semântica de cada campo e as garantias de privacidade/segredos.

        O `request_id` (quando propagado) é repassado adiante para ser gravado
        como campo, correlacionando o registro ao atributo `app.request_id` do
        span (Req 16.4).
        """
        log_request(
            question_hash=question_hash,
            provider=provider,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            used_fallback=used_fallback,
            attempts=attempts,
            result=result,
            request_id=request_id,
            logger=self._logger,
        )
