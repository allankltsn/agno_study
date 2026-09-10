"""Infraestrutura de conhecimento (RAG): fábrica de vector DB e Knowledge.

Este pacote concentra a montagem do RAG (vector DB + Knowledge da Agno). Como o
restante da infraestrutura de LLM, os imports da Agno são **tardios** (dentro das
funções), mantendo o módulo importável sem o SDK e confinando os tipos da Agno à
`infrastructure`.
"""
