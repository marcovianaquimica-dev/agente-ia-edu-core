# Phase 8C - Contract Baseline

Status: ETAPA 1A IMPLEMENTADA; ACEITACAO AINDA PENDENTE

## Contratos Definidos

- Criacao de proposta `PENDING` sem modificar questao, versao original ou item da lista.
- Aceitacao cria uma versao derivada e troca somente o `AssessmentItem` alvo.
- Cancelamento e repeticao segura de cancelamento/aceitacao.
- Respostas invalidas do provider (JSON, gabarito e alternativas) sao rejeitadas.
- Provider fake estruturado registra provider/modelo e suporta casos validos e invalidos.
- O texto potencialmente malicioso da questao e tratado como dado no contrato de prompt.

## Implementado

- Tabela `ModificationProposal` e migration reversivel `022_modification_proposals`.
- Adapter estruturado agnostico sobre `ProviderRouter`, com separacao entre politica, instrucao docente e dados nao confiaveis da questao.
- Criacao de proposta `PENDING` com validacao de JSON, alternativas, gabarito, dificuldade e tipo de modificacao.
- Limite configuravel por `QUESTION_MODIFICATION_MAX_PROPOSALS`.
- Cancelamento idempotente `PENDING -> CANCELLED`, sem alterar questao, versao ou item da lista.
- Autorizacao por escola, ownership da lista e universo pedagogico para professor.

## Estado Executado

- `tests/test_phase8c_question_modification.py`: `2 failed, 8 passed`.
- As duas falhas esperadas sao do endpoint `accept`, que permanece ausente por decisao de escopo.
- Nao ha UI e nao houve aplicacao de migration no banco principal.

## Invariantes para Implementacao

- A original permanece intacta; derivada aponta para `parent_version_id` da versao analisada.
- Apenas o item da lista no contexto da proposta pode mudar; outras listas mantem a versao original.
- Aceitacao deve ser transacional e idempotente.
- O provider deve ser consumido por adapter agnostico sobre `ProviderRouter`, com validacao estruturada antes de persistir proposta utilizavel.

## Proximo Passo

Implementar a Phase 8C Etapa 1B: aceitar proposta em transacao, criar `QuestionVersion` derivada, copiar alternativas/gabarito validado e alterar somente o `AssessmentItem` alvo. A UI de comparacao permanece fora deste incremento.