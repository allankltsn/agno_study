"""Camada de testes de integração/live com providers reais (Req 17.1).

Estes testes chamam providers de LLM **reais** (OpenAI/Groq) e, por isso, ficam
**fora** do gate de PR: são marcados com `@pytest.mark.integration` e só executam
quando as chaves de API exigidas estão presentes no ambiente. Sem as chaves eles
se auto-`skip`, de modo que nunca reprovam o gate rápido/determinístico.
"""
