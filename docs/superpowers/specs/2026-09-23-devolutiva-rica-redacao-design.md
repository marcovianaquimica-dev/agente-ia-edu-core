# Devolutiva rica de redação (aluno + professor)

**Data:** 2026-09-23
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto A** de três levas relacionadas (B: exportar PDF; C: dashboard de evolução do aluno em Redação — ambas fora de escopo aqui, tratadas em levas futuras).
**Depende de:** o fluxo de redação, o destaque visual de anotações e o reenvio de redação rejeitada — todos já mesclados em `main`.

---

## 1. Contexto

O usuário trouxe um modelo de referência (PDF, "Sua devolutiva de redação - modelo ENEM") muito mais rico do que a devolutiva atual: nota + faixas por competência já existem hoje, mas o modelo acrescenta um parágrafo de abertura pessoal, uma tabela "o que você já faz bem / onde pode avançar" por competência, blocos de reescrita vinculados a uma anotação específica (letra + competência), um checklist de revisão de C1 com ocorrências reais quando existirem, um plano de ação numerado, uma mensagem de fechamento do professor, e um aviso de transparência sobre a nota ser uma estimativa pedagógica.

Boa parte do conteúdo *já existe* no que a IA produz (`ai_output`) mas nunca é renderizado (`rewrites`, por exemplo, está no contrato desde R1 e nunca apareceu em nenhuma tela). Duas peças, porém, exigem **mudança no contrato da IA** (`essay_engine_contract`) e, por consequência, uma **nova versão do prompt de correção** (`essay_prompts`) — decisão já tomada com o usuário, não uma alternativa em aberto:

1. Vincular cada reescrita a uma anotação específica (letra + competência) — hoje `Rewrite` não tem esse vínculo.
2. Dividir o resumo por competência em "pontos fortes" / "onde pode avançar" — hoje é um parágrafo único.
3. Checklist de C1 com ocorrências reais confirmadas pela IA — não existe estrutura de dados para isso hoje.
4. Dois textos narrativos novos (abertura e mensagem de fechamento) — não existem hoje.

Pesquisei o mecanismo de versionamento de prompt antes de escrever este spec: `essay_prompts/v1.py` tem uma regra explícita ("Never edit this wording. A wording change is a new module (v2.py) plus a registry entry") e o pacote irmão `classification_prompts` já tem o precedente completo de como fazer isso (`v1.py` + `v2.py`, registro `_ARTIFACTS`, constante de versão no chamador). `essay_prompts/__init__.py` diz explicitamente que **não** quer um "default version" silencioso ("uma correção cujo prompt não pode ser identificado não pode ser auditada") — diferente de `classification_prompts`, que já tem `DEFAULT_VERSION`. Este spec respeita essa diferença: `essay_correction.py`'s `_PROMPT_VERSION` continua uma constante explícita, só que apontando pra `"essay_correction_v2"`.

Confirmei também (lendo `services/essay_correction.py`, tanto `correct()` quanto `retry()`) que **toda** chamada à IA — inclusive o reenvio de uma correção antiga que falhou (`retry()`) — usa a constante `_PROMPT_VERSION` do módulo, nunca o `prompt_version` já gravado na correção. Ou seja: a partir do momento em que a constante apontar pra v2, absolutamente toda produção nova de `ai_output` (mesmo retry de uma correção antiga) vai usar o prompt novo e preencher os campos novos. Isso simplifica a compatibilidade: os campos novos podem ser **obrigatórios** no contrato (a IA sempre vai ser instruída a preenchê-los daqui pra frente) — o único lugar que precisa tolerar ausência é o **frontend**, lendo `ai_output` de correções já aprovadas *antes* desta mudança (a rota já lê esse JSON como dict solto, sem revalidação — confirmado, nenhuma correção antiga vai quebrar, só vai faltar campo).

---

## 2. Escopo

### Entrega

**Contrato da IA (`src/agente_ia_edu/essay_engine_contract/v1.py`, mudança in-place — ver §6 sobre por que não precisa virar v2 do contrato):**
- `Rewrite` ganha `letter: str` (mesmo padrão de `Annotation.letter`, `^[A-Z]{1,2}$`) e `competency_code: CompetencyCode` — obrigatórios.
- `CompetencyRationale` ganha `strengths: str` e `growth_area: str` — obrigatórios, ambos `min_length=1`. `summary` continua existindo (não é removido — dado antigo depende dele, e pode servir de resumo curto em outros contextos).
- `EssayEngineOutput` ganha três campos novos, todos no nível raiz, irmãos de `feedback`:
  - `intro_message: str` (min_length=1) — parágrafo de abertura, pessoal, contextualizando a redação antes das notas.
  - `closing_message: str` (min_length=1) — mensagem de fechamento em tom de professor.
  - `mechanical_review: tuple[MechanicalOccurrence, ...] = ()` — lista de ocorrências confirmadas de erro mecânico (pode ser vazia; "nem todo texto terá todos estes erros").
- Novo submodelo `MechanicalOccurrence`: `category: Literal["ORTOGRAFIA","ACENTUACAO","CRASE","PORQUES","CONCORDANCIA","REGENCIA","PONTUACAO"]`, `excerpt: str` (o trecho exato da redação), `suggested_form: str` (a forma corrigida), `rule_explanation: str` (a regra, breve).
- Validação nova (camada 2, `essay_engine_validation.py`, mesma camada que já confere coerência de rubrica): todo `rewrite.letter` deve corresponder a um `annotations[].letter` existente — igual ao espírito da checagem de âncora que já existe pra `evidence_kind=LOCALIZED`.

**Prompt v2 (`src/agente_ia_edu/essay_prompts/v2.py`, novo arquivo — nunca editar v1.py):**
- `VERSION = "essay_correction_v2"`.
- `RESPONSE_SCHEMA` = a mesma estrutura de v1 (`scores`, `rationales`, `annotations`, `feedback`, `intervention`, `alerts` inalterados campo a campo) **mais**: `rationales[].strengths`/`.growth_area` (substituindo a descrição de `summary` sozinho), `rewrites[].letter`/`.competency_code`, `mechanical_review` (novo array), `intro_message`/`closing_message` (novos campos de nível raiz).
- Novo bloco de regras em português (mesmo estilo de `_RULES_COMMON`) instruindo: (a) todo `rewrite.letter` deve ser a letra de uma anotação já produzida na mesma resposta; (b) `mechanical_review` só deve conter ocorrências que a IA confirma existir no texto — nunca inventar erro pra preencher a lista; (c) `intro_message`/`closing_message` devem ser pessoais, em segunda pessoa, no mesmo tom pedagógico que `feedback.next_essay_strategy` já usa hoje.
- `build_prompt()` mantém a MESMA assinatura de v1 (`anchor_mode`, `essay_statement`, `rubric`, `include_scores`, `text`, `page_count`) — só o conteúdo montado muda.
- Registro em `essay_prompts/__init__.py`: `from . import v1, v2` e `_ARTIFACTS = {v1.VERSION: v1, v2.VERSION: v2}`. **Sem** `DEFAULT_VERSION` — mantém a filosofia "sempre explícito" já documentada no próprio módulo.
- `services/essay_correction.py:63`: `_PROMPT_VERSION = "essay_correction_v1"` → `"essay_correction_v2"`. Esta é a única mudança de comportamento em produção — todo `correct()`/`retry()` novo a partir daqui usa o prompt v2.

**Backend — enriquecer a devolutiva do aluno (`src/agente_ia_edu/api/routes/essay_submissions.py`):**
- `StudentCorrectionResponse` ganha campos novos, todos `Optional` no lado da RESPOSTA (não no contrato da IA — aqui é sobre o que pode faltar em dado antigo): `rationales`, `rewrites` (já existe, sem mudança de exposição — só o conteúdo interno ganha `letter`/`competency_code`), `intro_message`, `closing_message`, `mechanical_review`.
- `get_essay_submission_correction`: no ramo `APPROVED`, além dos campos já extraídos de `ai_output` (`annotations`, `rewrites`, `intervention`, `alerts`), passa a extrair também `rationales`, `intro_message`, `closing_message`, `mechanical_review` do mesmo jeito (`ai_output.get(...)`).
- A rota do professor (`GET /api/v1/teacher/essay-corrections`, `essay_corrections.py`) **não precisa de nenhuma mudança** — já expõe `ai_output` inteiro, sem filtro de campos (confirmado lendo o código: `EssayCorrectionResponse.ai_output: Optional[dict]`, passado direto). O painel do professor já tem acesso a tudo que este spec adiciona, só falta renderizar.

**Frontend — módulo de renderização compartilhado (novo arquivo, mesma exceção deliberada de convenção que `essay-annotations.js` já estabeleceu):**
- `src/agente_ia_edu/web/essay-report.js`, carregado nos dois portais, depois de `essay-annotations.js` (que ele reaproveita para os marcadores de texto/imagem já existentes) e antes de `essay.js`/`essay-review.js`.
- Função principal `window.EssayReport.renderRichReport(correction, { promptTitle, editable })` → retorna uma `string` HTML (mesmo padrão de `renderHighlightedText` em `essay-annotations.js`: função pura, quem chama decide onde inserir e quando religar eventos) contendo a devolutiva rica inteira: abertura, notas, tabela por competência (fortes/a melhorar, com fallback pra `summary` se os campos novos faltarem), reescritas agrupadas por letra/competência (com fallback: se `letter`/`competency_code` faltarem num item — dado antigo — mostra o bloco sem o cabeçalho de vínculo), checklist de C1 (tabela estática sempre presente + ocorrências dinâmicas de `mechanical_review` quando existirem), plano de ação, mensagem de fechamento (omitida se ausente), aviso de transparência (sempre presente, estático). `escFn: (value) => string` é um terceiro argumento (ou parte do objeto de opções — decisão de nomenclatura exata fica para o plano, mas a INJEÇÃO em vez de import é o requisito fixo). `editable` (booleano, default `false`) existe só para o painel do professor decidir se mostra a devolutiva como pré-visualização estática mesmo quando `correction.status` ainda é `PENDING_REVIEW` (onde o aluno veria "PENDING" genérico) — o professor sempre vê o conteúdo completo da IA, independente do status, já que é ele quem decide se aprova.
- Recebe um `escFn` (a função de escape do arquivo chamador — `escEssay` ou `tmEsc`) em vez de importar uma, mantendo a mesma disciplina de "escape sempre local ao arquivo, módulo compartilhado recebe injetado" já usada com sucesso na leva anterior.

**Frontend — integração nos dois portais:**
- `essay.js`: `renderApprovedDevolutiva` passa a delegar a maior parte da renderização pra `window.EssayReport.render(...)`, mantendo o botão "Voltar" e a navegação como já são.
- `essay-review.js`: o bloco de conteúdo do painel de revisão (`scoresFeedbackHtml`, tanto no estado `PENDING_REVIEW` editável quanto no terminal `APPROVED`/`REJECTED` somente-leitura) passa a incluir a mesma devolutiva rica **como pré-visualização somente-leitura** ao lado dos campos editáveis de nota/feedback que já existem — o professor vê exatamente o que o aluno vai receber, sem que isso interfira nos controles de aprovar/rejeitar já existentes (que continuam editando `final_scores`/`final_feedback`, não os campos novos, que são sempre do `ai_output` da IA).

### Não entrega — deliberadamente

- Exportar a devolutiva em PDF — leva B, spec própria depois desta.
- Dashboard de evolução do aluno (nota ao longo do tempo, por competência) — leva C, spec própria, provavelmente exige rota de backend nova agregando várias redações.
- Editar os campos novos (`intro_message`, `closing_message`, `mechanical_review`, a divisão fortes/a-melhorar) pelo professor — só `final_scores`/`final_feedback` continuam editáveis, como hoje. Os campos novos são sempre o que a IA produziu, sem cópia editável — mesmo padrão que `annotations`/`intervention`/`alerts` já seguem hoje (só existem dentro de `ai_output`, nunca em `final_*`).
- Reprocessar em lote as correções já aprovadas antes desta mudança para preencher os campos novos — ficam permanentemente sem essas seções (comportamento de fallback do frontend cobre isso, não é erro).
- Testes automatizados de frontend — mesmo corte de todas as levas anteriores.

---

## 3. Compatibilidade com dado antigo — resumo por campo

| Campo novo | Se ausente (correção aprovada antes desta leva) |
|---|---|
| `rationales[].strengths`/`.growth_area` | Mostra `rationales[].summary` como parágrafo único, sem a tabela de duas colunas |
| `rewrites[].letter`/`.competency_code` | Mostra o bloco de reescrita sem o cabeçalho "X - CN" (situação teórica: nenhum dado real hoje tem `rewrites` não-vazio, confirmado nos fixtures de teste) |
| `mechanical_review` | Mostra só a tabela estática de referência, sem nenhuma ocorrência dinâmica — mesmo estado que uma redação nova sem nenhum erro mecânico confirmado |
| `intro_message` | Seção de abertura omitida inteiramente |
| `closing_message` | Seção de mensagem do professor omitida inteiramente |

---

## 4. Autorização

Nenhuma rota nova, nenhuma mudança de padrão de autorização — tudo reaproveita `_authorize_student`/`_submission_for_own_school_or_403` (aluno) e `_authorize`/`_correction_for_own_school_or_403` (professor), já existentes, sem modificação.

## 5. O que fica para depois

Ver §2 "Não entrega". As levas B (PDF) e C (dashboard de evolução) ficam para specs próprias, cada uma com seu próprio brainstorm.

## 6. Por que o contrato (`essay_engine_contract`) não vira v2, só o prompt

O prompt tem uma regra explícita de imutabilidade porque uma mudança de texto pode alterar o COMPORTAMENTO da IA de forma sutil e difícil de auditar — por isso cada wording é uma versão nova, permanente, rastreável. O contrato (`essay_engine_contract`) é sobre FORMA estrutural, não texto de instrução; adicionar campos novos (opcionais no dado já armazenado, obrigatórios só na validação de saída nova) não tem esse mesmo risco de auditoria — é uma mudança de schema comum, do jeito que este mesmo projeto já fez repetidas vezes em `StudentCorrectionResponse`/`SubmissionContentResponse` nas levas anteriores. Não há precedente de versionamento de contrato neste código (`essay_engine_contract/__init__.py` não tem registro de versões), e criar um agora seria inventar uma convenção nova sem necessidade — a mudança em `v1.py` é aditiva e testável exatamente como qualquer outra mudança de schema já feita nesta sessão.

## 7. Rastreabilidade

- `src/agente_ia_edu/essay_engine_contract/v1.py` — `Rewrite`, `CompetencyRationale`, `EssayEngineOutput` — todos já existentes, ganham campos novos.
- `src/agente_ia_edu/essay_prompts/v1.py` — modelo de referência pro v2 (estrutura de `RESPONSE_SCHEMA`, `_RULES_*`, `build_prompt()` — mesma assinatura, nunca editado).
- `src/agente_ia_edu/essay_prompts/__init__.py` — `_ARTIFACTS`, padrão já usado por `classification_prompts/__init__.py` (`v1, v2` registrados, sem `DEFAULT_VERSION` aqui).
- `src/agente_ia_edu/services/essay_correction.py` — `_PROMPT_VERSION` (linha 63), `_run_ai` (confirmado: usa a constante do módulo tanto em `correct()` quanto em `retry()`, nunca o `prompt_version` já gravado).
- `src/agente_ia_edu/services/essay_engine_validation.py` — camada 2 (coerência de rubrica) ganha a checagem nova de `rewrite.letter` contra `annotations[].letter`.
- `src/agente_ia_edu/api/routes/essay_submissions.py` — `StudentCorrectionResponse`, `get_essay_submission_correction` — mesmo padrão de enriquecimento já usado para `canonical_text` e `resubmission_allowed` nas levas anteriores.
- `src/agente_ia_edu/api/routes/essay_corrections.py` — confirmado sem mudança necessária (`ai_output` já passa inteiro).
- `src/agente_ia_edu/web/essay-annotations.js` (leva anterior) — reaproveitado pelo novo `essay-report.js` para os marcadores de texto/imagem já existentes.
- `src/agente_ia_edu/web/essay.js`/`essay-review.js` — pontos de integração do novo módulo compartilhado.
