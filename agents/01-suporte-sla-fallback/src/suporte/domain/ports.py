"""Contratos (ports) e value objects do domínio.

Este módulo é o coração da **Inversão de Dependência (DIP)** do agente: a camada
de aplicação (`AnswerQuestion`) depende exclusivamente da abstração `LLMPort`
definida aqui, nunca de uma implementação concreta. Quem implementa a port são
os adapters de infraestrutura (`OpenAIAdapter`, `GroqAdapter`, `OllamaAdapter`) e
o `FallbackLLM` (Composite). Assim, o caso de uso permanece ignorante de rede,
de qual provider respondeu e da existência de fallback — e é testável apenas com
um duplo em memória.

Material de estudo (por que o domínio é Python puro):
- **Sem framework**: nenhum import de Agno, OpenAI ou Groq. A Agno só aparece
  dentro de `infrastructure/llm/*_adapter.py`, sempre envolvida (nunca vazada).
  Isso mantém o domínio estável e independente de detalhes de infraestrutura.
- **ISP (Interface Segregation)**: `LLMPort` expõe o contrato mínimo (`ask` e
  `ask_stream`). Clientes não são forçados a depender de métodos que não usam.
- **Value object imutável**: `Answer` é `frozen=True`, o que o torna seguro para
  compartilhar, comparar por valor e usar como resultado previsível de uma porta.

Compatibilidade: os tipos usados (`Iterator`, `Protocol`, `runtime_checkable`)
vêm de `typing` para funcionar em Python 3.9+.
"""

from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class Answer:
    """Value object imutável com a resposta e metadados de custo/observabilidade.

    Representa o resultado de uma chamada bem-sucedida a um provider de LLM. Por
    ser um *value object* imutável (`frozen=True`), duas `Answer` com os mesmos
    campos são iguais e nenhum campo pode ser reatribuído após a criação — o que
    evita mutação acidental ao propagar o resultado pelas camadas.

    Os campos de custo (`prompt_tokens`, `completion_tokens`) e de desempenho
    (`latency_ms`, `provider`) existem para alimentar o log estruturado e permitir
    conferir os critérios de eficiência operacional (latência por percentil, média
    de tokens, taxa de fallback) sem acoplar o domínio à observabilidade.

    Attributes:
        text: Texto da resposta destinada ao usuário.
        provider: Nome do provider que efetivamente respondeu (ex.: ``"openai"``).
            No fluxo de fallback, é o provider que teve sucesso, não o primário.
        prompt_tokens: Tokens de entrada consumidos (>= 0; zero quando omitido).
        completion_tokens: Tokens de saída consumidos (>= 0; zero quando omitido).
        latency_ms: Latência da chamada bem-sucedida, em milissegundos.
        out_of_scope: ``True`` quando o agente sinaliza que a pergunta não está
            fundamentada na documentação e deve ser encaminhada a um humano.
            Default ``False`` (dentro de escopo).
    """

    text: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    out_of_scope: bool = False

    @property
    def total_tokens(self) -> int:
        """Total de tokens consumidos, derivado da soma entrada + saída.

        É uma propriedade derivada (não um campo armazenado) para garantir que
        `total_tokens` nunca fique inconsistente com `prompt_tokens` e
        `completion_tokens`.

        Returns:
            Soma de ``prompt_tokens`` e ``completion_tokens``.
        """
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMPort(Protocol):
    """Contrato que a aplicação exige de qualquer provedor de LLM.

    É a abstração central da DIP: `AnswerQuestion` depende desta port, e não de
    adapters concretos. É implementada tanto pelos adapters concretos (que
    envolvem a Agno) quanto pelo `FallbackLLM` — que é um *Composite*: implementa
    a mesma `LLMPort` e, por dentro, delega a uma lista ordenada de outras
    `LLMPort` aplicando timeout, retry e fallback. Como o `FallbackLLM` "é uma"
    `LLMPort`, o caso de uso não distingue um adapter simples de um composto (OCP:
    acrescentar um provider não toca no domínio nem no caso de uso).

    Por ser um `Protocol`, a conformidade é **estrutural** (duck typing tipado):
    qualquer classe com as assinaturas `ask` e `ask_stream` satisfaz o contrato,
    sem herança explícita. O decorador `@runtime_checkable` permite usar
    `isinstance(obj, LLMPort)` em tempo de execução (verifica a presença dos
    métodos, não suas assinaturas), útil em testes e no composition root.
    """

    def ask(self, prompt: str) -> Answer:
        """Modo *run*: retorna a resposta completa de uma só vez.

        Args:
            prompt: A pergunta/prompt já montado a ser enviado ao provider.

        Returns:
            Um `Answer` com o texto e os metadados de custo/observabilidade.

        Raises:
            ProviderUnavailable: Quando o provider está indisponível (falha de
                rede, timeout ou autenticação). No `FallbackLLM`, é levantada
                somente após esgotar todos os providers da lista.
        """
        ...

    def ask_stream(self, prompt: str) -> Iterator[str]:
        """Modo *stream*: gera fragmentos de texto incrementalmente.

        Os fragmentos são emitidos na ordem de geração; concatená-los deve
        produzir um texto consistente em conteúdo com o `Answer.text` do modo
        `ask` para o mesmo prompt.

        Args:
            prompt: A pergunta/prompt já montado a ser enviado ao provider.

        Yields:
            Pedaços de texto (`str`) na ordem de emissão.

        Raises:
            ProviderUnavailable: Quando o provider está indisponível, seguindo o
                mesmo contrato de erro do método `ask`.
        """
        ...
