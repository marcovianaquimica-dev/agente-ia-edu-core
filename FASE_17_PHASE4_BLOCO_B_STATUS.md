# FASE 17 — PHASE 4 — BLOCO B STATUS

## A) IMPLEMENTADO

- Implementação do estudo e busca genérica do aluno em src/agente_ia_edu/services/study_search.py.
- Rota real do aluno em src/agente_ia_edu/api/routes/student.py.
- Reuso da arquitetura existente: KnowledgeService, authorization e catalog, sem camada paralela.
- Ajustes de visibilidade para questões e materiais em src/agente_ia_edu/services/knowledge.py.
- Arquivo de testes isolação em tests/test_student_study_search_isolation.py.

## B) COMPROVADO

### Execuções reais

- PYTHONPATH=src .venv/bin/python -m pytest tests/test_student_study_search.py -q
  - Resultado: 35 passed

- PYTHONPATH=src .venv/bin/python -m pytest tests/test_question_governance.py tests/test_question_governance_comprehensive.py tests/test_api_questions.py tests/test_student_study_search.py -q
  - Resultado: 81 passed

- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
  - Resultado: 520 testes executados, sem falhas e sem erros, em evidência anterior do repositório.

- PYTHONPATH=src .venv/bin/python -m compileall -q src tests
  - Resultado: compilação concluída sem erro reportado.

- .venv/bin/alembic heads
  - Resultado: 016_question_governance continua sendo a head.

- .venv/bin/alembic history
  - Resultado: histórico validado; sem nova migration criada.

## C) NÃO COMPROVADO

- Isolamento de tenant real para School A vs School B em HTTP completo.
- Isolamento de scope real para PUBLIC, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL e CLASSROOM.
- Prova de acesso real para aluno independente com school_id=None.
- Prova de grant de material privado para STUDENT_A e negação para STUDENT_B / STUDENT_INDEPENDENT.
- Prova de bypass por linguagem natural no caminho HTTP completo.

## D) PENDENTE

- Finalizar a matriz de testes de acesso real para School A / School B / aluno independente.
- Cobrir explicitamente UNIT, SEGMENT, GRADE_LEVEL e CLASSROOM em testes de backend e HTTP.
- Validar resultados reais de HTTP para /api/v1/student/search com as três identidades.
- Reexecutar a suíte completa após ajustar a infraestrutura do teste isolação.

## E) RISCOS

- A arquitetura funcional já está consistente, mas ainda não há evidência final de isolamento de tenant/scope no caminho HTTP real.
- O bloco não pode ser fechado sem prova de acesso por identidade, school_id e grants.
- O projeto está em auditoria de fechamento; não há commit ou push.

## STATUS FINAL

BLOCO B — NÃO CONCLUÍDO.
