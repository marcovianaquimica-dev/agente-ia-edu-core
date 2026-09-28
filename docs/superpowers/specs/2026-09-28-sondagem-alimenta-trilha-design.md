# Sondagem inicial passa a alimentar a Trilha de Estudos (PHASE 21)

**Data:** 2026-09-28
**Status:** design aprovado, aguardando plano de implementação
**Projeto:** `agente-ia-edu-core` (mesmo repositório)

---

## 1. Contexto e objetivo

Confirmado ao vivo, com dados reais (aluno real da Escola ABC completou uma sondagem de
10 questões, 3 corretas): o dashboard legado (`GET /api/v1/student/dashboard`) reflete a
sondagem corretamente (`has_data: true`, `overall_average: 30.0`, `questions_answered: 10`).
Mas a Trilha de Estudos nova (`GET /api/v1/student/study-path`, PHASE 21,
`AdaptiveLearningPathService`) devolve `state: "NO_EVIDENCE"`, `steps: []` para o MESMO
aluno, na MESMA hora — como se ele nunca tivesse respondido nada.

**Causa raiz**: `AdaptiveLearningPathService._build()` monta sua visão de "o que o aluno já
demonstrou saber" (`cstate`, por `content_code`) inteiramente a partir de
`CurriculumDomainMapService.get_map()`, que por sua vez é uma "projeção determinística do
histórico imutável de `ActivityResult`/`ActivityResultItem`" (docstring de
`curriculum_domain_map.py`) — o fluxo de listas de exercícios corrigidas (PHASE 18). A
sondagem inicial nunca cria `ActivityResult`; ela grava evidência real em
`StudentContentMastery` (`services/initial_diagnostic.py:839`, "Update
StudentContentMastery"), uma tabela agregada e completamente separada, que
`adaptive_learning_path.py` nunca consulta.

**Por que não simplesmente fazer `curriculum_domain_map.py` ler as duas fontes**: o
docstring desse módulo é explícito e deliberado sobre o oposto: *"It is separate from the
legacy taxonomy_nodes `student_content_mastery` (diagnostic/practice) - official-activity
and practice evidence are not merged."* Essa separação existe por design (evidência de
atividade oficial atribuída pelo professor tem uma natureza diferente de evidência de
prática/sondagem exploratória) e este trabalho não a desfaz. A integração correta acontece
uma camada acima, em `AdaptiveLearningPathService`, que é justamente onde múltiplas fontes
de evidência já deveriam convergir para a decisão final de "o que recomendar".

**Objetivo**: `AdaptiveLearningPathService._build()` passa a também consultar
`StudentContentMastery` do aluno, usando essa evidência como **fallback explícito** para um
`content_code` que não tem nenhuma evidência de atividade no domain map — nunca somando ou
misturando números das duas fontes (ver §3, decisão de design).

### Fora de escopo

- Qualquer mudança em `curriculum_domain_map.py` ou no modelo `ActivityResult`/
  `ActivityResultItem` — a separação documentada ali continua intacta.
- Qualquer mudança em `services/initial_diagnostic.py` — ele já grava `StudentContentMastery`
  corretamente; este trabalho só passa a LER isso de um lugar novo.
- O dashboard legado (`GET /api/v1/student/dashboard`) — já funciona corretamente e não é
  tocado.
- Fundir/ponderar numericamente evidência de diagnóstico com evidência de atividade quando
  AMBAS existem para o mesmo conteúdo (ver §3 — decisão explícita de não fazer isso agora).
- Qualquer coisa em redação/essay/devolutiva ou no fluxo de recepção.

---

## 2. Arquitetura

### 2.1 Onde a integração acontece

Em `AdaptiveLearningPathService._build()` (`src/agente_ia_edu/services/
adaptive_learning_path.py:371`), logo após `cstate` ser montado a partir do domain map
(linhas ~383-389: `cstate: dict[str, dict] = {}` populado por
`domain["disciplines"][*]["contents"][*]`, chaveado por `content_code`):

```python
# cstate já montado a partir do domain map (ActivityResult) aqui em cima
cstate = await self._fill_diagnostic_fallback(cstate, student_external_id)
```

Novo método privado `_fill_diagnostic_fallback(cstate, student_external_id)`: consulta
`StudentContentMastery` filtrando por `external_identity_id == student_external_id`, mapeia
cada `content_node_id` retornado para seu `content_code` real (via o mesmo grafo/`names` que
`_prereq_graph()` já carrega - reaproveitar, não duplicar essa resolução), e para cada
`content_code` **ausente** de `cstate` (nunca sobrescrevendo um que já existe vindo de
atividade real), insere uma entrada equivalente ao shape que o domain map já produz
(mesmos campos que `mastered()`/o resto de `_build` já espera:
`questions_answered`, `questions_correct`, `mastery_score` ou equivalente, `confidence`) A
PARTIR DOS VALORES REAIS de `StudentContentMastery` (`questions_answered`,
`questions_correct`, `mastery_score`, `confidence`) - nunca um valor inventado.

### 2.2 Transparência: marcar a origem da evidência

Cada entrada de `cstate` preenchida por este fallback ganha um campo novo
`evidence_source: "DIAGNOSTIC"` (contra `"ACTIVITY"` para o que já vinha do domain map,
default se o campo não existir hoje). Isso não é cosmético: a resposta de `/study-path` já
tem uma seção `transparency`/`provisional_note` (vistos na investigação ao vivo) que explica
ao aluno a origem da orientação - um passo derivado só da sondagem exploratória é uma
garantia mais fraca do que um derivado de prática deliberada em lista de exercícios, e o
aluno/professor deve poder ver essa diferença, não só confiar numa trilha que parece
uniforme. `StudentActionPlan`/schemas equivalentes do lado do professor (coordenação) que já
exibem proveniência de dados (ex.: `context_source: "TEACHER"` visto no dashboard legado)
estabelecem que esse tipo de campo já é um padrão aceito no projeto - não é uma convenção
nova.

### 2.3 Decisão de design: fallback, nunca fusão numérica

Quando um `content_code` tem evidência em AMBAS as fontes (aluno já fez uma lista de
exercícios sobre "Soluções" E também respondeu sobre "Soluções" na sondagem), a versão do
domain map (`ActivityResult`) **sempre vence, sem alteração nenhuma** - o método só
preenche o que está `ausente`. Rejeitei somar/ponderar as duas contagens porque:
- `StudentContentMastery.mastery_score`/`confidence` já são valores AGREGADOS e
  recalculados pelo próprio `initial_diagnostic.py` com sua própria fórmula (fixado nesta
  mesma sessão, ver commit do fix de "confiança fictícia") - misturar isso numericamente
  com a agregação independente do domain map (`definitive_evidence_count`/
  `provisional_evidence_count`) exigiria reconciliar duas fórmulas de confiança diferentes,
  o que é um projeto à parte, não uma correção de gap.
- Evidência de atividade real (lista atribuída, corrigida) é uma garantia mais forte que
  evidência de sondagem exploratória adaptativa - dar prioridade automática à primeira é a
  escolha mais conservadora e honesta.

---

## 3. Fluxo completo (exemplo real, reproduz o que foi visto ao vivo)

```
aluno completa a sondagem inicial (10 perguntas, 3 corretas)
  → initial_diagnostic.py grava StudentContentMastery para cada content_node_id
    tocado (ex.: "Soluções", "Funções", ...)
  → aluno abre a Trilha de Estudos (GET /api/v1/student/study-path)
  → AdaptiveLearningPathService._build():
      1. domain = CurriculumDomainMapService.get_map(...) → cstate a partir de
         ActivityResult (hoje: vazio, aluno nunca fez uma lista de exercícios)
      2. cstate = _fill_diagnostic_fallback(cstate, student_external_id) →
         para "Soluções"/"Funções" (ausentes em cstate), preenche com os
         valores reais de StudentContentMastery, evidence_source="DIAGNOSTIC"
      3. resto de _build() (prerequisitos, passos, candidatos) roda
         EXATAMENTE como já roda hoje, agora com cstate não-vazio
  → resposta: state != "NO_EVIDENCE", steps reais aparecem, cada um sabendo
    se sua evidência veio de atividade ou só da sondagem
```

---

## 4. Testes

- TDD para `_fill_diagnostic_fallback`: teste isolado (não precisa da rota HTTP) que
  confirma: (a) `content_code` presente só em `StudentContentMastery` aparece em `cstate`
  depois da chamada, com os valores reais; (b) `content_code` já presente em `cstate` (via
  domain map) NUNCA é sobrescrito, mesmo que `StudentContentMastery` tenha valores
  diferentes para ele; (c) aluno sem nenhuma linha em `StudentContentMastery` não quebra
  nada (retorna `cstate` inalterado).
- Teste de integração HTTP (`GET /api/v1/student/study-path`) reproduzindo o cenário real
  visto ao vivo: aluno com sondagem completa e ZERO atividades → `state` deixa de ser
  `NO_EVIDENCE`, `steps` não fica vazio.
- Regressão: aluno com evidência de ATIVIDADE real (não sondagem) continua funcionando
  exatamente como hoje - rodar a suíte já existente de `adaptive_learning_path`/`study-path`
  (`tests/test_r0_*`, `test_phase21_*`/equivalente - localizar o nome exato ao implementar)
  e confirmar 100% verde sem nenhuma asserção alterada.

---

## 5. Restrições globais

- Nunca editar `curriculum_domain_map.py` nem os modelos `ActivityResult`/
  `ActivityResultItem` - a separação documentada ali é intencional e permanece.
- Nunca editar `services/initial_diagnostic.py` - ele já grava `StudentContentMastery`
  corretamente.
- Nunca fabricar/inventar um valor de mastery/confiança - todo valor preenchido pelo
  fallback vem de uma linha real de `StudentContentMastery`.
- Evidência de atividade (domain map) sempre vence sobre evidência de sondagem quando as
  duas existem para o mesmo conteúdo - nunca fundir numericamente (ver §2.3).
- TDD obrigatório; suíte completa isolada verde antes de considerar concluído (checar
  `ps aux | grep pytest` e esperar execuções concorrentes de outras sessões terminarem -
  ambiente com Postgres descartável compartilhado na porta 5433).
- Nenhum `git commit`/`git push` sem o gatilho explícito do usuário.

---

## 6. Auto-revisão do spec

- **Placeholders**: nenhum. O único ponto deixado propositalmente aberto para a
  implementação é o nome exato do(s) arquivo(s) de teste já existente(s) para
  `adaptive_learning_path`/`study-path` (§4) - localizável por grep, não uma lacuna de
  comportamento.
- **Consistência interna**: §2.1-2.3 descrevem a mesma regra (fallback, nunca fusão) que
  §3 exemplifica; nenhuma contradição.
- **Escopo**: uma única mudança cirúrgica em um método de um service já existente: não
  cria tabela nova, não migra dado, não toca em três módulos adjacentes (domain map,
  diagnóstico, ActivityResult) cuja separação é deliberada.
- **Verificação da causa raiz**: toda a investigação (docstring de `curriculum_domain_map.py`,
  ausência confirmada de `StudentContentMastery` em `adaptive_learning_path.py` via grep,
  reprodução ao vivo do bug com um aluno real) foi feita por leitura direta do código e
  chamadas reais à API rodando, não presumida.
