# FASE 17 — PHASE 4 — BLOCO C STATUS

A) IMPLEMENTADO

- Persistência real de assignment e auditoria em [src/agente_ia_edu/db/models/assessments.py](src/agente_ia_edu/db/models/assessments.py):
  - AssessmentAssignment
  - AssessmentWorkflowAudit
- Serviço real de atribuição e transição em [src/agente_ia_edu/services/assessments.py](src/agente_ia_edu/services/assessments.py):
  - assign_list
  - get_assignment_status
  - mark_assignment_complete
  - list_workflow_audit
  - gravação de eventos de workflow em submit_review, approve, reject, publish e archive
- Autorização de escola e destinatário no mesmo serviço, sem confiar em input do cliente.
- Rotas de exercise list com controle de acesso por escola em [src/agente_ia_edu/api/routes/exercise_lists.py](src/agente_ia_edu/api/routes/exercise_lists.py).
- Migração Alembic em [migrations/versions/017_assignment_workflow_audit.py](migrations/versions/017_assignment_workflow_audit.py).
- Testes de regressão em [tests/test_exercise_lists.py](tests/test_exercise_lists.py) e prova HTTP em [tests/test_bloco_c_http.py](tests/test_bloco_c_http.py).

B) COMPROVADO

Evidências executadas e aprovadas:

- `cd /Users/marcoviana/agente-ia-edu-core && PYTHONPATH=src .venv/bin/python -m pytest tests/test_exercise_lists.py -q`
  - resultado: 8 passed in 1.88s
- `cd /Users/marcoviana/agente-ia-edu-core && PYTHONPATH=src .venv/bin/python -m pytest -q`
  - resultado: 578 passed, 8 warnings, 3 subtests passed in 66.61s
- `cd /Users/marcoviana/agente-ia-edu-core && PYTHONPATH=src .venv/bin/python -m compileall -q src tests`
  - resultado: OK
- `cd /Users/marcoviana/agente-ia-edu-core && .venv/bin/alembic heads`
  - resultado: 017_assignment_workflow_audit (head)
- `cd /Users/marcoviana/agente-ia-edu-core && .venv/bin/alembic upgrade head`
  - resultado: upgrade executado com sucesso
- Prova HTTP real: tentativa de acesso cross-school em `GET /api/v1/exercise-lists/{id}` retorna 403 quando o `school_id` do auth context difere do `school_id` do recurso.

C) NÃO COMPROVADO

- Nenhum gate restante para o BLOCO C após a correção da autorização de assignment e da prova HTTP.

D) PENDENTE

- Nenhum item pendente do BLOCO C.
- O avanço para Phase 5 deve permanecer bloqueado até aprovação formal da Phase 4 como um todo; nada mais do BLOCO C precisa ser implementado.

E) RISCOS

- Risco residual: qualquer adição de novo endpoint de assignment precisa reutilizar a mesma regra de school_id/recipient_id validada neste fechamento.
- Risco operacional: em produção, qualquer provider externo deve manter `school_id` do contexto autenticado consistente com o tenant real.
- Risco de falso positivo: continuar a confiar em payload do cliente sem validação backend em endpoints novos.

F) PRÓXIMO BLOCO RECOMENDADO

- Nenhum próximo bloco recomendado neste momento.
- O próximo passo adequado é a revisão final da Phase 4 e, somente após aprovação explícita, a abertura de Phase 5.

Conclusão: BLOCO C — CONCLUÍDO
