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
| atividade de outro aluno | **recusada** — 404 desde o hardening (§8) |
| aluno de outra turma | não vê na lista, e não abre |
| resultado de outro aluno | recusado |
| tentativa de outro aluno | recusada |
| finalizar incompleta | 409 |
| responder após finalizar | 409 |
| dupla finalização | **idempotente** (200, COMPLETED) |

### A dívida que eu havia deixado aqui — paga na §8

Escrevi o teste esperando 404 e recebi 403 com *"this activity is not assigned
to you"*, que confirma a existência a quem não tem acesso. Na época não mudei,
porque `AssignmentAuthError` é compartilhado e trocar seu status mexeria em
todos os consumidores.

**Resolvido sem tocar na exceção**: o que mudou foi o mapeamento HTTP das
rotas do aluno. Ver §8.

## 7. Testes

**19 novos** em `tests/test_atividade_oficial_e2e.py`, contra o app real:

- ciclo completo (abrir → responder → alterar → retomar → finalizar → corrigir)
- iniciar é idempotente; corrigir é idempotente; finalizar é idempotente
- finalizar incompleta e responder após finalizar são recusados
- evidência `OFFICIAL_ACTIVITY`, nunca `MICRO_DIAGNOSTIC`
- **bom e mau desempenho**, com o domínio e a readiness reagindo aos dois
- cinco cenários de autorização

## 8. Hardening (2026-10-04, depois do primeiro ciclo)

### Anti-enumeração

Recurso inexistente respondia 404; recurso real de **outro aluno** respondia
403 com *"this activity is not assigned to you"*. A diferença é um oráculo.

Nas rotas **do aluno**, "não existe" e "não é seu" passaram a responder
**igual** — mesmo status e mesmo corpo. Dez rotas: abrir, detalhe, estado,
salvar, posição, finalizar, corrigir, resultado, análise, prontidão.

As rotas de **gestão** continuam 403: lá o requester pode listar as
distribuições da escola, então esconder a existência não protege nada.
`AssignmentAuthError` não mudou — mudou como a camada HTTP do aluno a traduz.

O teste mede por **pares**: o par (inexistente, de outra turma) tem de ser
indistinguível. E há teste de que o dono continua entrando, porque uma trava
que barra todo mundo também passaria num teste mal escrito.

### Retomada: a dívida que eu havia registrado era falsa

Escrevi que "o cursor não é restaurado". Fui medir: `current_position` é
coluna de `ActivityAttempt`, `set_current_position` grava, `get_state`
devolve, e o frontend já lia. Responde Q1, navega até Q3, sai, volta —
**reabre na Q3**.

Cinco testes agora protegem isso, incluindo o que mais importa: uma posição
inválida é recusada (422) **sem apagar a posição boa**.

### Revisão

Sem endpoint novo. `/attempt` traz enunciado e alternativas; `/attempt/result`
traz o que ele marcou, o que era certo e a resolução. A tela junta pela
`question_version_id`.

O gabarito **não vaza** durante a tentativa — medido em três pontos:
`answer_key_visible: false`, nada de `correct_option_key` no estado, e nem ao
salvar resposta nem ao mover o cursor.

Acerto e erro não dependem de cor: cada alternativa relevante traz símbolo e
palavra ("✓ correta", "✗ sua resposta").

Abrir a revisão **não cria evidência** — teste abre cinco vezes e confere que
`questions_answered`, `evidence_count` e `origin_breakdown` não mudam.

### Responsividade

Dois transbordamentos reais, corrigidos sem media query nova:

| onde | em | media | causa |
|---|---|---|---|
| linha de busca | 320px | 486px | campo com 30px, "Enviar" fora da tela |
| cartão de progresso | 390px | 528px | botão "Revisar agora" não cabia |

O segundo **só aparece no estado "Precisa de atenção"**, porque só ele tem
botão — a auditoria com tudo "Consolidado" passava limpa. Apareceu no E2E
mobile, depois de errar a atividade de propósito.

A correção declara a largura em que o elemento deixa de ser útil
(`flex: 1 1 12rem` no campo, `1 1 8rem` no nome do conteúdo) e deixa a
composição quebrar sozinha. Abaixo disso os controles **crescem**, não
encolhem.

## 9. Dívidas

1. ~~403 vs 404~~ — **resolvido** nas rotas do aluno.
2. ~~Cursor não restaurado~~ — **era falso**, o cursor sempre funcionou.
3. ~~Resultado não mostra o gabarito~~ — **resolvido**, há tela de revisão.
4. **Não há "refazer".** Uma vez finalizada, a atividade não reabre — e não há
   caminho de produto para isso ainda.
5. **A atividade do piloto tem 5 questões porque eu escolhi 5**
   (`QUESTOES_NA_ATIVIDADE`). Não é regra de produto — é o tamanho de uma
   tarefa de casa, e serve para exercitar a navegação.
6. **O professor não vê o resultado da turma.** A evidência está gravada e a
   autorização de gestão existe, mas não há tela. É o maior buraco entre o que
   o sistema sabe e o que a escola enxerga.
