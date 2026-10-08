# Perfil Aluno — o ciclo pedagógico fechado

**2026-10-04.** Fecha a cadeia funcional do MVP do Aluno. A Home **não** foi
promovida: `/student/aluno.html` continua rota paralela.

> `atividade → conteúdos exigidos → evidência/domínio → prontidão →
> diagnóstico/preparação → sessão → respostas → domínio atualizado →
> persistência → Meu Progresso → próxima decisão`

---

## 1. O que foi descoberto antes de escrever código

Três premissas foram confirmadas **no código**, não de memória. Duas delas
mudaram o formato do trabalho.

### 1.1 Não havia motor para construir

`AdaptivePracticeService` (PHASE 22) já fazia quase tudo o que o
microdiagnóstico precisa. A docstring dele é quase o enunciado do bloco:

```
RECOMMENDATION → PRACTICE → ANSWER → CORRECTION → RESULT → DOMAIN → NEW PATH
```

`PracticeSelectionPolicy` já seleciona por `content_code` exato, exclui
dependência visual e questões protegidas, desprioriza as praticadas
recentemente, ordena de forma estável e **não usa IA**. E já se recusa a
concluir com banco insuficiente, devolvendo os três contadores.

**Então o microdiagnóstico não é um motor novo.** São três coisas que não
existiam: uma **origem** de evidência própria, uma **regra de parada** e a
**decisão**. Tudo o mais é o serviço existente, com quatro parâmetros
opcionais novos (`origin`, `metadata_extra`, `title`, `instructions`).

### 1.2 Os limiares já existiam e eram únicos

`PerformanceThresholdPolicy` se declara *"single source of truth for the
strong/improvement bands"* e já tem `band()`:

| banda | regra | faixa do aluno |
|---|---|---|
| `PONTO_FORTE` | `accuracy >= 0.80` | Consolidado |
| `DESEMPENHO_INTERMEDIARIO` | `0.60 ≤ accuracy < 0.80` | Em desenvolvimento |
| `PONTO_MELHORIA` | `accuracy < 0.60` | Precisa de atenção |
| `INSUFFICIENT_SAMPLE` | `answered < 3` | Ainda estamos conhecendo seu aprendizado |

Meu Progresso **traduz**; não decide. Há teste que varre a árvore sintática
do módulo de tradução e falha se qualquer literal `0.6` ou `0.8` aparecer
como constante avaliada.

**O "3" do microdiagnóstico também sai daí.** Ele pergunta
`min_sample_size` à política em vez de escrever o número. Há teste que
constrói o serviço com `min_sample_size=5` e exige que ele peça 5 questões.
Isso também é o que torna a **parada antecipada** estrutural: a regra
pergunta se a política já consegue decidir, em vez de contar até três. Com a
política de hoje ela nunca dispara antes da terceira resposta — e isso é
correto, não uma limitação.

### 1.3 A armadilha era real, e tinha duas camadas

`curriculum_domain_map.py` resolvia origem desconhecida assim:

```python
key = origin if origin in _KNOWN_ORIGINS else ORIGIN_OFFICIAL_ACTIVITY
```

O dado dizia explicitamente *"não sou atividade oficial"* e o código
convertia em atividade oficial, **em silêncio**. Um microdiagnóstico com
origem não registrada viraria avaliação da escola, e o professor passaria a
ver um desempenho que a escola nunca mediu.

**A investigação encontrou dois defaults parecidos, e só um era o problema:**

| onde | caso | veredito |
|---|---|---|
| linha ~400 | assignment **sem** `origin` no metadata → oficial | **legítimo.** Toda atividade distribuída por professor, e tudo anterior à PHASE 22, de fato é oficial. **Preservado**, com teste travando. |
| linha ~138 | origin **presente mas desconhecido** → oficial | o bug. Sem teste, sem chamador, sem dado dependente. **Fechado.** |

Ausente significa oficial; presente-mas-irreconhecível significa desconhecido.

**Fail-closed, não fail-silent.** Origem desconhecida vai para um balde de
quarentena `UNKNOWN_ORIGIN`, deliberadamente **fora** de `_KNOWN_ORIGINS`
para que ninguém possa declará-la. Não levanta exceção: uma linha estranha
derrubaria a tela de progresso inteira do aluno, o que é pior que o risco.
Há teste exigindo que a soma dos baldes continue batendo com `answered` —
quarentena que some é pior que quarentena nenhuma.

---

## 2. A tabela de fechamento

| elo | antes | depois | real/mock | teste |
|---|---|---|---|---|
| atividade → conteúdos | não exposto | `content_codes` agregado de `content_question_links` | **real** | `test_aluno_cadeia_conteudos.py` (13) |
| conteúdos → domínio | existia | inalterado | **real** | `test_phase20_domain_map.py` |
| domínio → prontidão | existia | inalterado | **real** | `test_aluno_ciclo_pedro.py` A/B/C |
| prontidão → `DIRECT` | só no protótipo | persistida | **real** | `test_aluno_rota_persistida.py` |
| prontidão → `DIAGNOSTIC` | rota inalcançável | alcançável pelo gatilho real | **real** | caso B |
| diagnóstico → evidência | **não existia** | `MicroDiagnosticService` | **real** | `test_aluno_microdiagnostico.py` (10) |
| diagnóstico → decisão | **não existia** | `decidir()`, bandas da política | **real** | caso B |
| lacuna → preparação | existia (prática) | inalterado | **real** | caso C |
| tempo → sessão | existia | inalterado | **real** | caso D |
| sessão → persistência | colunas vazias | serviço grava | **real** | `test_aluno_rota_persistida.py` (10) |
| respostas → domínio | existia | preserva origem nova | **real** | caso C, ciclo medido |
| domínio → Meu Progresso | **mock** | `GET /student/progress` | **real** | `test_aluno_meu_progresso.py` (17) |
| interrupção → retomada | parcial | objetivo preservado | **real** | caso D |

### O ciclo, medido

Não é afirmação: são os números de uma execução real do caso Pedro.

| momento | respondidas | acerto | origens | prontidão |
|---|---:|---:|---|---|
| início | — | — | — | `DIAGNOSTIC` |
| 5 de Balanceamento, 1 acerto | 5 | 0,20 | `{PRACTICE: 5}` | `PREREQUISITE_PREPARATION` |
| +9, todas certas | 14 | 0,71 | `{PRACTICE: 14}` | `DIAGNOSTIC` |

A terceira linha é a prova de que a decisão usa o **novo** estado: o
pré-requisito deixou de bloquear, e a rota voltou a pedir diagnóstico porque
continuamos sem saber nada sobre Estequiometria em si. Pedagogicamente certo.

---

## 3. Arquivos, contratos, migrations

**Alterados:** `services/curriculum_domain_map.py` (origens + fail-closed) ·
`services/adaptive_practice.py` (4 parâmetros opcionais) ·
`services/study_session.py` (rota, objetivo, `marcar_objetivo_concluido`) ·
`api/routes/student.py` (`GET /progress`) · `web/aluno.js` (Meu Progresso real)

**Novos:** `services/micro_diagnostic.py` · `services/student_progress.py`

**Contrato de API:** `GET /api/v1/student/progress` (novo, só leitura) ·
`QBStudentActivity.content_codes` (do bloco anterior).

**Migrations:** nenhuma neste bloco. A `064` já existia e foi usada.

**Origens de evidência:** `OFFICIAL_ACTIVITY` · `PRACTICE` ·
**`MICRO_DIAGNOSTIC`** (nova) · `INITIAL_DIAGNOSTIC` · `SIMULADO`, mais a
quarentena `UNKNOWN_ORIGIN`, que não é declarável.

---

## 4. O que continua mock

| item | situação |
|---|---|
| `aluno.js` lê `MOCK.tarefas` | `content_codes` existe no contrato, a tela ainda não o consome |
| frases de "Meu progresso" (`fatos`) | mock — o **panorama** é real, as três frases do topo não |
| prontidão no protótipo | `MOCK.dominio`, não `/learning-path` |
| texto livre → assunto | sem interpretação |
| foto / arquivo / voz | desabilitados, com rótulo honesto |

---

## 5. Dívidas e decisões pendentes

1. **A Home continua não promovida.** Falta a tela consumir `content_codes` e
   `/learning-path` de verdade.
2. **Quem chama o microdiagnóstico?** O serviço existe e está testado; nenhum
   endpoint o expõe ainda. Foi deliberado: expor é decisão de produto sobre
   quando interromper o aluno.
3. **As três frases do topo de Meu Progresso** continuam mock.
4. **`UNKNOWN_ORIGIN` não tem alarme.** Hoje ele contém o dado estranho e
   aparece no breakdown; ninguém é avisado se aparecer em produção.
5. **A suíte de frontend é quase toda estática.** Três vezes neste projeto um
   guarda que varre o fonte cru encontrou a própria explicação em comentário —
   duas delas hoje. Os testes novos varrem a árvore sintática (Python) ou
   removem comentários antes (JS), mas é remendo: um DOM de verdade mediria
   melhor.

---

## 6. Comportamentos fail-closed acrescentados

| onde | antes | agora |
|---|---|---|
| origem desconhecida | virava `OFFICIAL_ACTIVITY` em silêncio | vai para `UNKNOWN_ORIGIN` |
| rota de prontidão inválida | erro de integridade no meio do commit | `StudySessionError` antes do banco |
| banco sem questões para diagnosticar | — | `INSUFFICIENT_EVIDENCE`, sem conclusão |
| domínio ilegível em Meu Progresso | — | diz que não conseguiu, não inventa faixa |
