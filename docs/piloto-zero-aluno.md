# Piloto Zero — Escola ABC / Aluno Teste A

**2026-10-04.** O primeiro ciclo pedagógico completo que dá para usar com as
mãos, não só com testes.

---

## 1. Como acessar

```bash
PYTHONPATH=src DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" .venv/bin/python -m uvicorn agente_ia_edu.api.app:create_app --factory --port 8099
```

Depois abra **http://localhost:8099/student/aluno.html** e digite a
identidade:

```
aluno_teste_a
```

Sem senha. É o mesmo mecanismo DEV dos outros portais —
`TestExternalIdentityProvider`, a identidade vai como Bearer. **Nenhum
sistema de autenticação novo foi criado neste bloco.**

> Sem o prefixo `student:`. O provedor de identidade o remove antes de montar
> o contexto, e um vínculo gravado com prefixo nunca casa — o sintoma é
> silencioso: a lista de tarefas volta vazia. Há linhas assim no banco de
> dev, vindas de seeds antigos, e elas de fato não funcionam.

## 2. O que testar (roteiro)

1. Abrir a página e entrar como `aluno_teste_a`.
2. Ver o cartão: **Atividade de Estequiometria**, entrega 2026-10-07, e o
   convite a responder três perguntas sobre **Balanceamento**.
3. Tocar em **Responder**.
4. Confirmar que o topo mostra `🎯 Para: Atividade de Estequiometria` — a
   tarefa da escola continua sendo o objetivo.
5. Responder as três questões (são do Núcleo Diagnostic Bank).
6. Ver a conclusão: *"Essa parte você sabe. Agora falta Estequiometria."*
7. Ir em **Meu progresso** e conferir que Balanceamento aparece.
8. Voltar ao Início: o cartão agora pede diagnóstico de **Estequiometria**.
9. Para repetir: `python scripts/reset_piloto_zero.py`.
10. Para provar o isolamento: entrar como `alice` — nenhuma tarefa.

## 3. O que é real

| funcionalidade | real / mock | fonte |
|---|---|---|
| tarefa da escola | **real** | `GET /student/activities` → `ActivityAssignment` |
| conteúdos que a tarefa exige | **real** | `content_codes` ← `content_question_links` |
| prontidão (3 rotas) | **real** | `GET /activities/{id}/readiness` → `ReadinessRouteService` |
| microdiagnóstico | **real** | `POST /micro-diagnostic` → `MicroDiagnosticService` |
| as questões respondidas | **real** | 14 itens AI_VERIFIED do Diagnostic Bank |
| player e correção | **real** | `ActivityPlayerStore` / `ActivityCorrectionStore` |
| evidência | **real** | `origin_breakdown: {MICRO_DIAGNOSTIC: 3}` |
| domínio e nova decisão | **real** | `rebuild_student` + `decidir` |
| Meu Progresso | **real** | `GET /student/progress`, faixas de `PerformanceThresholdPolicy` |
| abrir a atividade e resolvê-la | **não existe** | a tela diz isso |
| foto / arquivo / voz | **estático** | controles nascem `disabled` |
| interpretar texto livre | **estático** | a nota diz que não interpreta |

**Saíram neste bloco:** o objeto `MOCK` inteiro (oito estados de Home,
prazos, dificuldade, pré-requisitos inventados) e os três "fatos" de Meu
Progresso — *"Você estudou 3 dias esta semana"* era uma frase fixa que
parecia uma medida.

## 4. O fluxo pedagógico

```
Atividade de Estequiometria (a escola mandou)
   └─ exige CHEMISTRY-PHYSICAL-STOICHIOMETRY
        └─ estado do aluno: INSUFFICIENT_EVIDENCE
             └─ rota: DIAGNOSTIC
                  └─ alvo: CHEMISTRY-GENERAL-BALANCING  ← o pré-requisito
                       └─ 3 questões do Diagnostic Bank
                            └─ correção determinística
                                 └─ evidência MICRO_DIAGNOSTIC
                                      └─ domínio recalculado
                                           └─ nova decisão: o alvo sobe
                                                para Estequiometria
```

**A tarefa da escola tem prioridade, não exclusividade.**
`objective_assignment_id` é preservado e `objective_completed` continua
`false` o tempo todo: preparar-se para a atividade não é concluí-la, e o
diagnóstico não aparece como tarefa entregue.

### Começar pela base

O planejador só diz `BLOCKED_BY_PREREQUISITE` quando **já tem evidência** de
que o pré-requisito é fraco. No primeiro acesso não há evidência de nada, e o
conteúdo final sai como `INSUFFICIENT_EVIDENCE` sem bloqueio — o que mandaria
diagnosticar Estequiometria em quem talvez não saiba balancear uma equação.

Entre dois conteúdos igualmente desconhecidos, **o de baixo informa mais**:
se ele souber, a conversa sobe sozinha; se não souber, a resposta já é a que
precisávamos. Foi também o que destravou o piloto na prática — o banco
diagnóstico é de Balanceamento, e sem essa regra o aluno era mandado a um
diagnóstico de Estequiometria que tem **uma** questão no acervo.

## 5. Dados criados

| | |
|---|---|
| escola | **Escola ABC** (já existia) — marcada `hosts_pilot_zero` |
| série | 3ª Série (nova, Ensino Médio) |
| turma | **Turma 3ª A (Piloto)**, `external_id = PILOTO_3A` |
| aluno | **Aluno Teste A** — Person + User + Student + StudentEnrollment + UserSchoolLink |
| professor | `professor_abc` (já existia) — é quem cria e distribui |
| atividade | Atividade de Estequiometria, CLASS → `PILOTO_3A`, `origin=OFFICIAL_ACTIVITY` |
| Diagnostic Bank | 14 itens AI_VERIFIED carregados no banco de dev |

Nada disso é mock: tudo passa pelas tabelas e pelos serviços reais. A
**turma própria** é o que isola o piloto — a Escola ABC já tinha 1ª Série /
Turma A com outro aluno, e misturar os dois faria o sandbox aparecer para
quem abrisse aquela turma.

### O Diagnostic Bank entrou no banco de dev

Estava bloqueado pela divergência das migrations 063. Com a reconciliação
(`066_merge_063_lineages`, bloco anterior) a dívida nº 1 de
`docs/diagnostic-bank-v1.md` **fechou**: os 14 itens saíram de
`/tmp/diagnostic_v2.json` para `scripts/data/` e para o banco, com edição
própria (instituição Núcleo, caderno `BALANCEAMENTO-V1`, gabarito próprio).

## 6. Isolamento

Medido, não afirmado — `test_piloto_zero_e2e.py`, casos I e J:

| quem | vê a atividade do piloto? |
|---|---|
| `aluno_teste_a` | sim |
| `alice` (outra escola) | **não** — lista vazia, e `404` ao perguntar pelo id |
| `professor_abc` | **não** como tarefa dele (vínculo SCHOOL, não CLASSROOM) |

O `404` é deliberado: um `403` confirmaria que a atividade existe.

## 7. Reset

```bash
PYTHONPATH=src .venv/bin/python scripts/reset_piloto_zero.py
```

Apaga só o **histórico** do Aluno Teste A: respostas, tentativas,
resultados, domínio, e as atividades que ele mesmo iniciou.

Preserva — conferido depois de rodar: 575 questões, 2.972 extrações, 556
classificações, os 14 itens diagnósticos, as 4 escolas, os outros alunos e
**a atividade da escola** (ela é da turma, criada pelo professor; apagá-la
seria apagar trabalho da escola).

O aluno em si não é derrubado: recriar Person/User/Student arrastaria
matrícula e vínculo. O que torna o teste repetível é ele não ter histórico —
então é o histórico que sai.

## 8. Três defeitos reais encontrados ao fazer isto

**1. O microdiagnóstico aparecia como tarefa da escola.**
`student_activities` excluía `origin == "PRACTICE"` — uma lista-negra, que só
conhece as origens que existiam quando foi escrita. O dia em que o
microdiagnóstico ganhou origem própria, ele virou "dever de casa" na tela do
aluno. Agora é lista de permissão: só `OFFICIAL_ACTIVITY` conta.

**2. Quem errava tudo na base era liberado para a atividade.**
`MicroDiagnosticService.decidir` só mandava preparar quando havia *outro*
conteúdo a preparar. Diagnosticando Balanceamento — que não tem
pré-requisito — um aluno com acerto **0.0** saía como
`PROCEED_TO_ACTIVITY`. Medido, não imaginado. Evidência fraca agora sempre
manda preparar; `prerequisite_code = None` passa a significar "o que precisa
de preparo é este conteúdo mesmo".

**3. A decisão de prontidão morava no navegador.**
Quatro linhas de JavaScript sobre um `MOCK`. Qualquer pessoa com o console
aberto mudava o próprio diagnóstico. Virou `services/readiness_route.py`, com
os três valores importados de `study_session.py` — se divergirem do
CheckConstraint da migration 064, o banco recusa a escrita.

## 9. Dívidas que afetam o Piloto Zero

1. **A atividade de Estequiometria tem UMA questão.** É o acervo real: só
   uma questão de `CHEMISTRY-PHYSICAL-STOICHIOMETRY` está publicada,
   classificada em definitivo e com caderno. Não completei com questão
   inventada. Consequência prática: depois de dominar Balanceamento, o
   diagnóstico de Estequiometria **não abre** — faltam questões, e a tela
   diz exatamente isso em vez de concluir algo.
2. **Resolver a atividade não existe.** A tela de entrada mostra o título e a
   nota do próprio backend (*"a resolução será disponibilizada em breve"*).
   O player existe e funciona — é o mesmo que o diagnóstico usa —, falta
   ligá-lo à atividade oficial.
3. **28 testes de renderização saíram.** Eram regex sobre o `aluno.js` do
   protótipo; estão em `tests/prototipo_aluno_pre_piloto/` com a explicação.
   O que cobriam de microcópia hoje só é verificado à mão no navegador.
4. **A rota não é persistida em `study_sessions`.** A coluna
   `readiness_route` existe (064) e o endpoint devolve o valor, mas o Piloto
   Zero não abre uma `StudySession` — o diagnóstico vai direto. Professor e
   Coordenação vão precisar desse registro.
