# A atividade oficial — o último elo

**2026-10-04.** O aluno chegava em *"Pronto para começar"*, clicava, e lia que
a resolução seria disponibilizada em breve. Agora ele resolve.

---

## 1. O bloqueio era uma string

```python
"entry_screen": {
    "heading": assessment.title,
    "action_label": "Iniciar atividade",
    "note": "A resolução da atividade será disponibilizada em breve.",   # ← aqui
}
```

`activity_assignment_store.py:518`. **Texto fixo, não gate.** Na mesma resposta,
`can_start` já vinha `true`, e o `ActivityPlayerStore.start` aceitava a
tentativa sem reclamar.

O backend estava inteiro desde a PHASE 17/18 — e é o **mesmo** que o
microdiagnóstico e a prática usam há três blocos. O que faltava era a tela:
`abrirTarefa()` em `aluno.js` renderizava o `note` em vez de abrir o player.

**Nenhuma migration. Nenhuma tabela nova. Nenhum endpoint novo.**

## 2. O mapa, e onde parava

| elo | implementação | antes | depois |
|---|---|---|---|
| UI abrir | `aluno.js abrirTarefa` | mostrava o `note` | **abre o player** |
| iniciar | `POST /activities/{id}/attempt` | existia | usado |
| questões | `ActivityPlayerState.questions` | existia | renderizadas |
| navegar | `PUT .../attempt/position` | existia | **Anterior / Próxima** |
| responder | `PUT .../attempt/answers/{vid}` | existia | usado |
| alterar | o mesmo PUT | existia | usado |
| retomar | `GET .../attempt` | existia | usado |
| finalizar | `POST .../complete` | existia | usado |
| corrigir | `POST .../correct` | existia | usado |
| resultado | `ActivityResult` / `...Item` | existia | mostrado |
| evidência | `metadata.origin` do assignment | existia | `OFFICIAL_ACTIVITY` |
| domínio | `POST /domain/rebuild` | existia | **chamado após corrigir** |
| histórico | `GET /progress` | existia | reflete |

Duas linhas do frontend eram de fato novas: o **rebuild do domínio** após a
correção, e o estado **"Entregue"** no cartão.

## 3. O que a atividade oficial tem e o diagnóstico não

**Navegação.** O microdiagnóstico é linear — três perguntas, sem volta, porque
o sistema está decidindo um caminho. A tarefa da escola não: o aluno volta,
pula, revê e muda de ideia antes de entregar.

"Finalizar" só aparece com todas respondidas. O backend recusa de qualquer
jeito (409); oferecer o botão antes seria oferecer um erro.

## 4. Proveniência: conclusão ≠ domínio

```
origin_breakdown: {"MICRO_DIAGNOSTIC": 3, "OFFICIAL_ACTIVITY": 5}
```

Já era distinguível — `curriculum_domain_map` lê `metadata.origin` do
assignment, e a atividade do piloto nasce com `OFFICIAL_ACTIVITY`. Não foi
preciso criar enum, coluna nem tabela.

**Origem da evidência ≠ qualidade da evidência.** Medido nos dois caminhos:

| | acertos | domínio | readiness | Meu Progresso |
|---|---|---|---|---|
| **bom** | 5/5 | accuracy 1.0 | `DIRECT` | Consolidado |
| **ruim** | 0/5 | accuracy 0.0 | **`PREREQUISITE_PREPARATION` → `PRACTICE`** | **Precisa de atenção** |

O aluno que entregou a tarefa inteira e errou tudo continua sendo tratado como
alguém que precisa estudar. A política é a autoridade; entregar não é saber.

## 5. Um defeito que só apareceu na tela

Com 0 de 5, o backend dizia "Precisa de atenção" e a tela dizia **Consolidado**.

Causa: o frontend não reconstruía o domínio após finalizar. No microdiagnóstico
isso acontece sozinho, porque `/micro-diagnostic/{id}/decision` reconstrói
antes de decidir — a atividade oficial não tem endpoint equivalente, então a
tela lia o estado anterior à correção.

Corrigido com uma chamada a `POST /domain/rebuild` logo após `correct`.

**Só foi visível no navegador.** Por API, cada passo estava certo.

## 6. Isolamento (§3)

| cenário | resultado |
|---|---|
| atividade inexistente | 404 |
| atividade de outro aluno | **recusada** (403) |
| aluno de outra turma | não vê na lista, e não abre |
| resultado de outro aluno | recusado |
| tentativa de outro aluno | recusada |
| finalizar incompleta | 409 |
| responder após finalizar | 409 |
| dupla finalização | **idempotente** (200, COMPLETED) |

### A dívida que escolhi não pagar

Escrevi o teste esperando **404** e recebi **403** com a mensagem *"this
activity is not assigned to you"* — o que **confirma que a atividade existe**
a quem não tem acesso.

Não mudei. `AssignmentAuthError` é compartilhado por prática, diagnóstico e
atividade; trocar seu status mexeria no contrato de autorização de todos os
consumidores, e isso é decisão de produto, não ajuste de teste.

Fica a inconsistência registrada: `/readiness`, que criei neste piloto, diz
**404**; o player diz **403**. As duas recusam; só uma esconde a existência.

## 7. Testes

**19 novos** em `tests/test_atividade_oficial_e2e.py`, contra o app real:

- ciclo completo (abrir → responder → alterar → retomar → finalizar → corrigir)
- iniciar é idempotente; corrigir é idempotente; finalizar é idempotente
- finalizar incompleta e responder após finalizar são recusados
- evidência `OFFICIAL_ACTIVITY`, nunca `MICRO_DIAGNOSTIC`
- **bom e mau desempenho**, com o domínio e a readiness reagindo aos dois
- cinco cenários de autorização

## 8. Dívidas

1. **403 vs 404** na autorização (acima). Decisão de produto.
2. **Sem `position` no retorno do player para a UI** — o frontend usa
   `current_position` para retomar, mas o backend não o devolve em
   `get_state`; a tela sempre reabre na primeira questão depois de sair. As
   respostas são preservadas; só o cursor não.
3. **Não há "refazer".** Uma vez finalizada, a atividade não reabre — e não há
   caminho de produto para isso ainda.
4. **O resultado não mostra o gabarito.** `answer_key_visible: true` vem na
   resposta e os itens trazem `correct_option_key`, mas a tela só mostra o
   placar. Revisar o que errou é o próximo passo natural.
5. **A atividade do piloto tem 5 questões porque eu escolhi 5**
   (`QUESTOES_NA_ATIVIDADE`). Não é regra de produto — é o tamanho de uma
   tarefa de casa, e serve para exercitar a navegação.
