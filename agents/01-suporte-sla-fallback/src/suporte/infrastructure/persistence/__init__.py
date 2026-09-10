"""Infraestrutura de persistência (memory & sessions): fábrica de Db do Agno.

Concentra a montagem do `Db` do Agno (DynamoDB por padrão; SQLite como fallback
local). Como o restante da infraestrutura, os imports da Agno e do boto3 são
**tardios** (dentro das funções), mantendo o módulo importável sem os SDKs e
confinando os tipos da Agno à `infrastructure`.
"""
