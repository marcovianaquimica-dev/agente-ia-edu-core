# Feedback estruturado de C2/C3 + explicação específica de crase/pontuação

**Data:** 2026-09-29
**Status:** design aprovado, aguardando plano de implementação

---

## 1. Contexto

O usuário pediu 3 mudanças na correção de redação por IA:

1. Erro de crase envolvendo pronome deve nomear o tipo de pronome e explicar a regra específica, em vez de uma explicação genérica de crase. Melhorar também a avaliação de pontuação (vírgula).
2. Renomear e reestruturar a Competência 2 ("Compreensão do tema" → "Tipologia, tema e repertório"), com o feedback decomposto em 3 aspectos (Tipologia textual / Tema / Repertório sociocultural), com uma regra explícita contra inventar repertório quando não houver.
3. Renomear e reestruturar a Competência 3 ("Argumentação" → "Projeto argumentativo e autoria"), com o feedback decomposto em 3 aspectos (Projeto argumentativo / Informações-fatos-opiniões / Autoria) mais uma orientação de melhoria, sempre ancorada em evidência real do texto, nunca inventada.

Pontuação (0/40/80/120/160/200) não muda em nenhuma competência — só nomenclatura, apresentação e estrutura do feedback.

Pesquisei o código antes de propor o design:

- **Labels curtos das competências estão hardcoded e duplicados em 4 lugares**, todos com o mesmo texto literal, sem nenhum registro central: `web/essay-evolution.js` (`COMPETENCY_LABELS`), `web/essay-report.js` (`COMPETENCY_LABELS` + `COMPETENCY_DESCRIPTIONS`), `services/essay_pdf_export.py` (`_COMPETENCY_LABELS`), `services/essay_teacher_dashboard.py` (`_COMPETENCY_LABELS`). O rubric YAML (`rubrics/enem_2025.yaml`) só tem o `official_title` completo (frase longa), não um label curto.
- **O feedback por competência hoje é `CompetencyRationale`** (`essay_engine_contract/v4.py:217-224`): `summary`/`strengths`/`growth_area`, texto livre corrido, igual para as 5 competências. Não há decomposição em aspectos - só `signal_keys` referencia as chaves de `signals` do rubric, mas o texto em si não é estruturado por aspecto.
- **`renderCompetencyChecklist`** (`web/essay-report.js:32-55`) é a ÚNICA função que renderiza essa tabela de feedback, e é **compartilhada**: usada pelo relatório rico do aluno (`essay.js`) e do professor (`essay-review.js`, painel de pré-visualização), e chamada diretamente também pelo gráfico de evolução (`essay-evolution.js`). `services/essay_pdf_export.py` tem o equivalente em Python (`_competency_table_html`), documentado no próprio código como espelhando deliberadamente a mesma lógica de fallback.
- **`MechanicalOccurrence`** (`essay_engine_contract/v4.py:300-319`) já tem `category` (incluindo `CRASE` e `PONTUACAO`) + `excerpt` + `suggested_form` + `rule_explanation` (texto livre, por ocorrência específica - já ancorado a um trecho, não um comentário genérico de C1). A regra de prompt hoje (`_RULES_MECHANICAL_REVIEW`, `essay_prompts/v14.py:381-388`) trata as 7 categorias de forma idêntica, sem orientação específica pra crase ou pontuação.
- **Convenção de versionamento já estabelecida no projeto** ("nunca editar, mudança de shape é módulo novo"): `essay_engine_contract/__init__.py` e `essay_prompts/__init__.py` documentam isso explicitamente. Prompts têm um registro central (`_ARTIFACTS` em `essay_prompts/__init__.py`); contratos são importados diretamente pela versão (`essay_correction.py` importa `essay_engine_contract.v4`).

Decisões já tomadas com o usuário durante o brainstorm (não reabrir):

1. Correções já publicadas continuam sob o contrato/prompt antigos (v4/v14), sem recorreção - a UI usa fallback pro texto livre de hoje quando os campos novos não existirem. Consistente com o "primeiro calibrar, depois recorrigir" já em vigor pra essa área do sistema.
2. C2 ganha um campo de orientação de melhoria (`c2_orientacao_melhoria`), por simetria com C3, mesmo o pedido original do usuário não tendo mencionado isso explicitamente pra C2.
3. Crase-por-pronome e pontuação/vírgula **não** viram campo estruturado novo - a melhoria fica só no texto do prompt v15, orientando a IA a nomear o tipo de pronome e ser mais específica na regra de pontuação, dentro do `rule_explanation` que já existe por ocorrência. Decisão pela eficiência (nenhuma mudança de schema necessária) e porque uma taxonomia fechada de "tipo de pronome"/"tipo de regra de vírgula" arriscaria ficar incompleta ou forçar casos de borda numa categoria errada. Fica como possível próximo passo, condicionado a medir se o texto sozinho resolve.

---

## 2. Escopo

### Entrega

**Contrato novo (`essay_engine_contract/v5.py`, cópia de v4 + mudanças):**

`EssayEngineOutput` ganha 8 campos novos, todos `str` obrigatório (`min_length=1`) quando `SCORING_MODE=AVALIATIVO`:

```
c2_tipologia_textual, c2_tema, c2_repertorio_sociocultural, c2_orientacao_melhoria,
c3_projeto_argumentativo, c3_fatos_informacoes_opinioes, c3_autoria, c3_orientacao_melhoria
```

`rationales` (a lista genérica de `CompetencyRationale`) passa a conter só C1, C4 e C5 - C2 e C3 saem dela (evita a IA escrever o mesmo conteúdo duas vezes, uma genérica e outra estruturada).

**Prompt novo (`essay_prompts/v15.py`, cópia de v14 + mudanças):**

- `RESPONSE_SCHEMA` atualizado: os 8 campos novos substituem as entradas de C2/C3 em `rationales`.
- Regra nova pro repertório de C2: quando nenhum repertório for identificado, `c2_repertorio_sociocultural` deve usar exatamente o texto que o usuário especificou: "Não foi identificado repertório sociocultural no texto. Para fortalecer sua argumentação, procure utilizar referências pertinentes ao tema, como fatos históricos, conceitos, pesquisas, dados, obras, legislação ou outros conhecimentos socioculturais, relacionando-os ao argumento desenvolvido."
- Regra nova pra C3: toda afirmação em `c3_fatos_informacoes_opinioes` e `c3_autoria` deve ser ancorada em evidência real do texto - nunca inventar fato, exemplo ou traço de autoria que não esteja na redação. Quando não houver desenvolvimento suficiente de algum aspecto, dizer isso explicitamente (não fingir que existe).
- Regra nova pra `mechanical_review`: quando `category=CRASE` e o motivo envolver pronome, `rule_explanation` deve nomear o tipo de pronome (demonstrativo/relativo/pessoal oblíquo/indefinido/possessivo) e explicar a regra específica daquele tipo, nunca uma explicação genérica de crase. Quando `category=PONTUACAO`, critério mais detalhado sobre quando a vírgula é exigida (não só "falta vírgula", mas por qual construção - aposto, vocativo, oração intercalada, adjunto deslocado, etc.).

**Serviço (`services/essay_correction.py`):**

- `_PROMPT_VERSION` passa a `"essay_correction_v15"`; import de `essay_engine_contract.v5` no lugar de `.v4`; `essay_engine_validation.py` idem.
- v4/v14 permanecem intocados no repo - correções antigas persistidas sob esses nomes continuam legíveis por eles.

**Frontend - labels (4 arquivos, texto idêntico em todos):**

`web/essay-evolution.js`, `web/essay-report.js` (`COMPETENCY_LABELS` e `COMPETENCY_DESCRIPTIONS`), `services/essay_pdf_export.py` (`_COMPETENCY_LABELS`), `services/essay_teacher_dashboard.py` (`_COMPETENCY_LABELS`) - C2 e C3 trocam de texto, C1/C4/C5 sem mudança.

**Frontend - tabela de feedback (`renderCompetencyChecklist` em `essay-report.js` + `_competency_table_html` em `essay_pdf_export.py`, a MESMA lógica nas duas linguagens):**

Para C2 e C3, quando os campos novos existirem na correção, renderiza os aspectos na ordem definida (C2: Tipologia textual / Tema / Repertório sociocultural / Como melhorar; C3: Projeto argumentativo / Informações, fatos e opiniões / Autoria / Como melhorar), cada um com seu rótulo. Quando os campos novos não existirem (correção antiga, contrato v4), cai no comportamento atual (`strengths`/`growth_area`, ou `summary` como último fallback). C1/C4/C5 sem mudança nenhuma.

Como essa função é compartilhada, a mudança propaga automaticamente para: relatório rico do aluno, painel de pré-visualização do professor, gráfico de evolução (chama a função diretamente) e exportação em PDF - sem precisar tocar em cada consumidor.

### Não entrega - deliberadamente

- Recorreção de redações já publicadas sob o contrato antigo - ficam com o feedback de texto livre de sempre.
- Mudança na escala de pontuação de C2 ou C3 (0/40/80/120/160/200 continua igual) - só nomenclatura, apresentação e estrutura do feedback.
- Campo estruturado dedicado para "tipo de pronome" (crase) ou "tipo de regra" (pontuação) - fica só como texto melhorado dentro do `rule_explanation` que já existe por ocorrência, decisão do brainstorm.
- Mudança em C1, C4 ou C5 além de saírem da lista `rationales` junto com a remoção de C2/C3 dela (a estrutura de `strengths`/`growth_area` continua igual para essas três).
- Qualquer mudança na lógica de CLASSIFICAÇÃO/pontuação de C2 ou C3 (ex: a fase 2a de pontuação por competência) - fica pra uma calibração separada, como o próprio usuário definiu.

---

## 3. Modelo de dados (contrato v5)

```python
# essay_engine_contract/v5.py - novos campos em EssayEngineOutput, resto igual a v4

c2_tipologia_textual: str = Field(min_length=1)
c2_tema: str = Field(min_length=1)
c2_repertorio_sociocultural: str = Field(min_length=1)
c2_orientacao_melhoria: str = Field(min_length=1)

c3_projeto_argumentativo: str = Field(min_length=1)
c3_fatos_informacoes_opinioes: str = Field(min_length=1)
c3_autoria: str = Field(min_length=1)
c3_orientacao_melhoria: str = Field(min_length=1)

# rationales: tuple[CompetencyRationale, ...] - agora só C1, C4, C5
# (validação: nenhuma rationale pode ter competency_code C2 ou C3 quando SCORING_MODE=AVALIATIVO)
```

Todos os 8 campos são obrigatórios só quando há nota (mesmo padrão condicional que `scores`/`rationales` já usam para FORMATIVO vs AVALIATIVO).

---

## 4. Renderização (fallback de 3 níveis, mesma lógica em JS e Python)

Para C2/C3, ao montar a linha da tabela:

1. **Campos estruturados novos existem?** → renderiza os aspectos, um por linha/parágrafo, na ordem definida.
2. **Senão, `strengths`/`growth_area` existem?** (correção v4 com o split já feito) → comportamento atual, duas colunas.
3. **Senão** → `summary` genérico, célula única (fallback mais antigo, já existe hoje).

Para C1/C4/C5: sempre nível 2 (comportamento atual, sem mudança).

---

## 5. Testes

- Contrato v5: campos novos obrigatórios quando AVALIATIVO; `rationales` rejeita `competency_code` C2/C3.
- Prompt v15: `RESPONSE_SCHEMA` tem os 8 campos; regra do repertório vazio presente no texto do prompt; regra de evidência real (C3) presente; regra de crase-por-pronome e pontuação detalhada presentes no bloco de `mechanical_review`.
- Serviço: correção nova usa v15/v5; correção antiga (mockada com shape v4) continua sendo lida sem erro por rotas que hoje leem `ai_output`.
- Frontend (`renderCompetencyChecklist`/`_competency_table_html`): os 3 níveis de fallback, testados com fixtures que têm (a) os 8 campos novos, (b) só `strengths`/`growth_area`, (c) só `summary` - confirma qual nível renderiza em cada caso, pra C2 e C3; confirma que C1/C4/C5 nunca mudam de comportamento.
- Labels: os 4 arquivos mostram o texto novo de C2/C3, texto de C1/C4/C5 inalterado.

---

## 6. Global Constraints

- Nunca editar `essay_engine_contract/v4.py` nem `essay_prompts/v14.py` - toda mudança de shape é `v5.py`/`v15.py`, novos módulos.
- Correções persistidas sob `essay_engine_output_v4`/`essay_correction_v14` continuam legíveis exatamente como hoje - sem migração de dados, sem recorreção.
- Pontuação de C2/C3 (escala 0/40/80/120/160/200) não muda nesta leva.
- Nenhuma afirmação de fato/exemplo/repertório pode ser inventada pela IA quando não existir no texto do aluno - regra explícita no prompt v15, para C2 (repertório) e C3 (fatos/autoria).
- `renderCompetencyChecklist`/`_competency_table_html` continuam sendo o único ponto de renderização do feedback por competência - toda mudança de exibição entra ali, nunca duplicada nos consumidores (aluno/professor/evolução/PDF).
