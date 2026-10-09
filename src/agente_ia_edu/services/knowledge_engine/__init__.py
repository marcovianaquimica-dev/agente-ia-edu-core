"""CEREBRO / Knowledge Engine - o subsistema de corpus de conhecimento.

NUNCA defina aqui uma classe chamada ``KnowledgeService``:
``services/knowledge.py`` ja ocupa esse nome e e outra coisa (consulta
relacional do catalogo pedagogico). ``tests/test_knowledge_engine_boundary.py``
falha se alguem reusar o nome.

O Knowledge Pack e a UNICA interface publica deste subsistema. Chunks,
vetores e termos sao internos, e o mesmo teste de fronteira falha se algum
modulo de fora importar os modelos do corpus.
"""
