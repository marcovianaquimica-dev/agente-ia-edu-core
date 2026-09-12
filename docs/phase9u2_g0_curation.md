# PHASE 9U.2-G0 — Human Curation of the 21 Unclassified Official Questions

> **Status:** FRAMEWORK READY — individual fiches **PENDING** (blocked on question statement text).
> This is a curation/analysis artifact only. **No code, DB, migration, catalog node,
> retrieval vocabulary, classification, or provider call is produced by this phase.**

---

## 0. G0-RESUME log

- **2026-09-07 — G0-RESUME attempt.** Instruction was to read the 21 statements
  read-only *directly from the database* in this session. **Blocked:** the assistant's
  execution environment has **no `DATABASE_URL`** (`get_database_url()` raises
  `DatabaseConfigurationError`; `.env` contains only `POSTGRES_USER/PASSWORD/DB/N8N_DB`
  — local Docker credentials, not a valid production substitute). The
  `QUESTION_TEXT_EXTRACTION_PASS` output from the operator's own terminal run was never
  delivered into the conversation, so no question text is available to the assistant.
  Curation of the 21 fiches (§7) therefore **remains PENDING**. Unblock: either grant this
  session a production `DATABASE_URL`, or paste the extraction command's full stdout once.
  No prior content of this document was changed.

---

## 1. Objective

Determine, from the **real text of each question**, *what curricular content each of the
21 currently-unclassified official questions is actually assessing* — precisely enough to
feed:

- **9U.2-G1** — Taxonomy Design (which catalog nodes / codes to create)
- **9U.2-G2** — Controlled Vocabulary Design (which `RetrievalVocabularyEntry` to author)

This phase does **not** decide how to represent the content technically. It only answers
*"o que cada questão está cobrando?"*.

---

## 2. Curation rules (binding)

1. Work **only** from the real question text (`question_versions.statement` /
   `canonical_text`, plus alternatives where available). **Never infer content from the
   official number.**
2. Do **not** modify, insert, update or delete any classification or any other data.
3. Do **not** run Alembic / migrations / INSERT / UPDATE / DELETE / automatic
   classification / provider / OpenAI.
4. Do **not** create catalog nodes or `RetrievalVocabularyEntry` objects. When a new
   content seems required, record only a **conceptual suggestion** (e.g. *"conceptual
   suggestion: chemical-equilibrium topic"*) — never a final `CODE`.
5. Do **not** create a new `taxonomy_version`. Do not touch `023_curriculum_taxonomy` or
   `024_chemistry_kinetics`.
6. **Do not force a classification.** If a question reasonably fits more than one content,
   set `CURATION_CONFIDENCE = MEDIUM`/`LOW` and list every plausible alternative. If it
   cannot be determined safely, set `VOCABULARY_STATUS = HUMAN_REVIEW_REQUIRED`. Never
   choose arbitrarily.
7. **Content ≠ vocabulary.** A question can clearly belong to a content even when no
   catalog node and no vocabulary exist for it yet — that is an infrastructure gap, not
   "no content".
8. **The matcher is not the pedagogical authority.** "The matcher found nothing" is an
   infrastructure limitation, **not** evidence the question is outside the curriculum.

---

## 3. Current state (from the 9U.2-F / 9U.2-G diagnostics — do not re-derive)

### Existing catalog nodes (treat as the only nodes that exist)

```
CHEMISTRY
  CHEMISTRY-PHYSICAL
    CHEMISTRY-SOLUTIONS
      CHEMISTRY-SOLUTIONS-CONCENTRATION
      CHEMISTRY-SOLUTIONS-DILUTION
    CHEMISTRY-PHYSICAL-KINETICS          (migration 024)
PHYSICS
  PHYSICS-MECHANICS
    PHYSICS-MECHANICS-KINEMATICS
      PHYSICS-MECHANICS-KINEMATICS-UNIFORM
BIOLOGY
  BIOLOGY-CYTOLOGY
    BIOLOGY-CYTOLOGY-BIOCHEMISTRY
      BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS
MATH
  MATH-ALGEBRA
    MATH-ALGEBRA-FUNCTIONS
      MATH-ALGEBRA-RATIO
```

### Existing retrieval vocabularies

| taxonomy_version | canonical content code | registered in `_INITIAL_CONTROLLED_VOCABULARIES` | usable in INITIAL binding |
|---|---|---|---|
| `024_chemistry_kinetics` | `CHEMISTRY-PHYSICAL-KINETICS` (`KINETICS_RETRIEVAL_VOCABULARY`, version `phase9t3-kinetics-v1`) | YES (only entry) | YES |

No other retrieval vocabulary exists anywhere in `src/`.

---

## 4. Scope — the exact 21 questions

Source of the set: `var/inep-pilot/enem2020_d2_cd5_import_report.json` (23 questions
`created=True`) intersected with the 9U.2-E production inventory (`TOTAL_OFFICIAL_QUESTIONS = 24`).

- **Imported into the Question Bank (`created=True`, 23):**
  `92, 93, 94, 95, 103, 104, 105, 107, 111, 112, 113, 125, 126, 127, 128, 129, 130, 133, 134, 135, 152, 153, 172`
- **Protected (NOT part of the 21):** `Q91` (historical `023_curriculum_taxonomy` row),
  `Q93` (ACTIVE INITIAL `024`), `Q128` (ACTIVE INITIAL `024` + 1 SUPERSEDED). `Q91` was
  imported in a separate Phase-9E run and is the 24th official question.
- **The 21 unclassified official questions (curation targets):**

```
92, 94, 95, 103, 104, 105, 107, 111, 112, 113, 125, 126, 127, 129, 130, 133, 134, 135, 152, 153, 172
```

### Structural discipline split (from booklet layout only — NOT from question text)

Per `PHASE_9A_OFFICIAL_INVENTORY_AND_PDF_PILOT.md`, ENEM 2020 D2 CD5 booklet:
items **91–135 = Ciências da Natureza** (Química / Física / Biologia, mixed), items
**136–180 = Matemática**.

| Booklet block | Questions among the 21 | Count | Note |
|---|---|---|---|
| Ciências da Natureza (91–135) | 92, 94, 95, 103, 104, 105, 107, 111, 112, 113, 125, 126, 127, 129, 130, 133, 134, 135 | 18 | discipline (Química vs Física vs Biologia) is **NOT** determinable from position — needs text |
| Matemática (136–180) | 152, 153, 172 | 3 | discipline = MATH by position; area/content needs text |

> This block split is a structural fact of the printed booklet. It is **not** a curation
> result. Area and content for every question still require the statement text.

---

## 5. BLOCKER — why the fiches are PENDING

Curation rule §2.1 requires the **real statement text** of each question, read from
`question_versions` / `booklet_questions` in the database. In the environment where this
document was drafted:

- `DATABASE_URL` is **not available** — the production database cannot be read.
- The local Docker database must **not** be used as a substitute (established across
  Phases 9U.1–9U.2).
- The raw ENEM PDFs in `var/inep-pilot/` are **CC BY-ND** licensed; `PHASE_9A` states
  *"uso e redistribuição exigem avaliação jurídica antes de qualquer distribuição de
  itens."* Reproducing 21 item statements into a committed document is out of bounds, and
  PDF-question ↔ `question_version_id` matching still requires the database.
- The import report (`enem2020_d2_cd5_import_report.json`) contains only
  `number / position / created / review_required / reason / annulled` — **no text**.

Therefore the 21 individual fiches cannot be completed responsibly here. Filling them from
the question number alone is explicitly forbidden (§2.1, §6, and 9U.2-G rule 13).

---

## 6. Completion protocol (how a curator finishes this document)

1. **Extract the curation input** — run the existing READ-ONLY diagnostic against the
   production `DATABASE_URL` (it already dumps, per question: a sanitized statement
   excerpt, existing classification fields, `recover_candidates` output, kinetics
   `match_retrieval_vocabulary` result, and a fixed-keyword `discipline_signals` scan):

   ```bash
   cd /Users/marcoviana/agente-ia-edu-core
   PYTHONPATH=src .venv/bin/python tests/manual/phase9u2f_coverage_diagnostic.py
   ```

   If the ~200-char excerpt is insufficient for confident curation, a curator with lawful
   access reads the full `question_versions.statement` (+ options) for the 21 numbers
   directly — **read only**.

2. **For each of the 21 questions, fill the fiche** in §7 using the statement text as the
   sole authority. Record short verbatim evidence excerpts (a few words), the probable
   discipline → area → content, whether an existing node covers it, whether a new
   vocabulary is needed, and a confidence level. Where ambiguous: list alternatives, drop
   confidence, or mark `HUMAN_REVIEW_REQUIRED`.

3. **Populate the grouping** in §8 and the summary counts in §10.

4. Hand the completed document to **9U.2-G1** (taxonomy design) and **9U.2-G2**
   (vocabulary design). No node/vocabulary/code is created until then.

---

## 7. Individual curation fiches (21) — SUPERSEDED

> **This section's PENDING templates are superseded by §15 (G0-RESUME, 2026-09-07),**
> which contains the 21 completed evidence-backed fiches based on the real question text
> from `scratchpad_question_text_9u2.txt`. The template below is kept for history.

Fiche template (repeat for each number). Fill `PENDING` fields from the statement text.

```
OFFICIAL_NUMBER:            <n>
QUESTION_VERSION_ID:        PENDING (from booklet_questions → question_versions)
BOOKLET_BLOCK:              <Ciências da Natureza | Matemática>   (structural, from §4)

DISCIPLINA_PROVAVEL:        PENDING   (QUÍMICA | FÍSICA | BIOLOGIA | MATEMÁTICA)
AREA_CURRICULAR_PROVAVEL:   PENDING
CONTEUDO_CURRICULAR_PROVAVEL: PENDING
SUBCONTENT_PROVAVEL:        PENDING (optional)

CONTENT_CODE_EXISTENTE:     PENDING   (one of §3 existing codes, or "none")
CONTENT_CODE_NOVO_PROVAVEL: PENDING   (CONCEPTUAL SUGGESTION ONLY — e.g. "tópico de equilíbrio químico"; no CODE)

CATALOG_NODE_STATUS:        PENDING   (EXISTING_NODE | NEW_NODE_REQUIRED | UNCERTAIN)
VOCABULARY_STATUS:          PENDING   (EXISTING_VOCABULARY | NEW_VOCABULARY_REQUIRED | HUMAN_REVIEW_REQUIRED)
CURATION_CONFIDENCE:        PENDING   (HIGH | MEDIUM | LOW)

RATIONALE:                  PENDING
EVIDENCE:                   PENDING   (short verbatim excerpts from the question, ≤ ~12 words each)
ALTERNATIVES (if MEDIUM/LOW): PENDING
```

### Fiches

- **#92**  — BOOKLET_BLOCK: Ciências da Natureza — *all other fields PENDING*
- **#94**  — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#95**  — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#103** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#104** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#105** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#107** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#111** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#112** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#113** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#125** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#126** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#127** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#129** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#130** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#133** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#134** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#135** — BOOKLET_BLOCK: Ciências da Natureza — PENDING
- **#152** — BOOKLET_BLOCK: Matemática — PENDING
- **#153** — BOOKLET_BLOCK: Matemática — PENDING
- **#172** — BOOKLET_BLOCK: Matemática — PENDING

---

## 8. Grouping (skeleton — populate after fiches)

Fill each group with `count`, `question numbers`, `proposed content`, `justification`.

### A) Contents that ALREADY have a catalog node
*(questions whose content maps to one of the §3 existing codes — expected small, e.g. a
solutions/dilution item, a uniform-kinematics item, a proteins/biochemistry item)*

- PENDING

### B) Contents that need a NEW catalog node
*(everything else — e.g. chemical equilibrium, thermochemistry, electrochemistry, organic
chemistry, dynamics, thermology, optics, waves, electromagnetism, genetics, evolution,
ecology, physiology; plus the MATH areas of #152/#153/#172)*

- PENDING

### C) Questions that can SHARE one retrieval vocabulary
*(questions on the same content — one `RetrievalVocabularyEntry` serves all)*

- PENDING

### D) Questions that need DISTINCT vocabularies
*(one content each)*

- PENDING

### E) Ambiguous / HUMAN_REVIEW_REQUIRED
*(multi-content, cross-disciplinary, figure-dependent, or otherwise not safely
determinable from text)*

- PENDING

---

## 9. Protected questions — recorded, NOT curated

| Question | Status | Existing classification | Action |
|---|---|---|---|
| **Q91** | PROTECTED | historical `023_curriculum_taxonomy` row `id=18b8666b…`, `classification_mode=NULL`, `lifecycle=ACTIVE`, `content=""`, `source=ai`, `model_version=phase9e-question91-v1` | **DO NOT ALTER.** Flagged in 9U.2-A/F as a historical row itself requiring human review — outside this phase. |
| **Q93** | PROTECTED | ACTIVE INITIAL `024_chemistry_kinetics`, content `CHEMISTRY-PHYSICAL-KINETICS` | **DO NOT ALTER.** |
| **Q128** | PROTECTED | ACTIVE INITIAL `024_chemistry_kinetics` (`CHEMISTRY-PHYSICAL-KINETICS`, corrected in 9U.1) + 1 SUPERSEDED historical (`CHEMISTRY-SOLUTIONS`) | **DO NOT ALTER.** |

These three are **not** part of the 21 and take no curation.

---

## 10. Summary (populate after fiches)

```
TOTAL_ANALYZED:                 21   (targets: 92,94,95,103,104,105,107,111,112,113,125,126,127,129,130,133,134,135,152,153,172)
QUESTIONS_HIGH_CONFIDENCE:      PENDING
QUESTIONS_MEDIUM_CONFIDENCE:    PENDING
QUESTIONS_LOW_CONFIDENCE:       PENDING
EXISTING_CATALOG_CONTENTS:      PENDING   (subset of §3 codes actually used)
NEW_CATALOG_CONTENTS_REQUIRED:  PENDING   (conceptual list, no codes)
NEW_VOCABULARIES_REQUIRED:      PENDING
HUMAN_REVIEW_REQUIRED:          PENDING
CURATION_GROUPS:                PENDING   (A/B/C/D/E from §8)
```

### Final table (populate after fiches)

```
OFFICIAL_NUMBER | DISCIPLINE | AREA | CONTENT | NODE_STATUS | VOCABULARY_STATUS | CONFIDENCE | EVIDENCE | RATIONALE
----------------+------------+------+---------+-------------+-------------------+------------+----------+----------
92   | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING
94   | PENDING | ...
95   | PENDING | ...
103  | PENDING | ...
104  | PENDING | ...
105  | PENDING | ...
107  | PENDING | ...
111  | PENDING | ...
112  | PENDING | ...
113  | PENDING | ...
125  | PENDING | ...
126  | PENDING | ...
127  | PENDING | ...
129  | PENDING | ...
130  | PENDING | ...
133  | PENDING | ...
134  | PENDING | ...
135  | PENDING | ...
152  | MATH    | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING
153  | MATH    | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING
172  | MATH    | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING
```

---

## 11. Recommendations for 9U.2-G1 / 9U.2-G2 (structural — independent of the pending text)

These do not depend on the fiches and can be adopted now as design constraints:

**For G1 (Taxonomy Design):**
- Adopt **one stable `taxonomy_version` per catalog generation** (e.g. `curriculum-v2`),
  not one per content. Freeze `023_curriculum_taxonomy` / `024_chemistry_kinetics` as
  historical tags. Rationale: `taxonomy_version` is the partition key of migration 025's
  partial unique index; minting a new tag per content would let a question hold two ACTIVE
  rows in two partitions undetected.
- New catalog nodes must be **additive** (migration 024's guarded-`INSERT` +
  guarded-downgrade pattern). Define a `catalog_nodes.code` naming convention
  (`DISCIPLINE-AREA-CONTENT[-SUBCONTENT]`, uppercase, hyphen-separated) before creating
  any code; `code` is globally unique.
- Q93/Q128 stay on `024_chemistry_kinetics`. New classifications for the 21 target the new
  generation tag. Re-tagging an existing classification across generations is a separate,
  explicitly-authorised sub-phase — never bundled here.

**For G2 (Controlled Vocabulary Design):**
- Each new `RetrievalVocabularyEntry`: mandatory `canonical_code` = a live
  `catalog_nodes.code` (`node_type=CONTENT`, `active`); ≥ 1 primary/specific/contextual
  phrase required to match; `generic_terms` audit-only (never trigger); immutable
  `version` string once used in production; deterministic + order-independent; fail-closed
  (no lexical evidence → `None` → `OUT_OF_SCOPE`, never a guess); one `canonical_code` per
  entry.
- Mandatory tests per vocabulary: positive match → BOUND; generic-only → no match;
  adjacent-topic → no match (false-positive resistance); matched-but-canonical-absent →
  NEEDS_REVIEW; dedup/merge-priority determinism; order independence.
- The INITIAL binding is currently locked to kinetics at **3 code points**
  (`_INITIAL_CONTROLLED_VOCABULARIES` dict, `recover_candidates`'s hard-coded
  `(KINETICS_RETRIEVAL_VOCABULARY,)` tuple, and `_validate_target_taxonomy`'s
  `!= KINETICS_TAXONOMY_VERSION` + `KINETICS_PARENT_CODE` checks). Generalising these is a
  real `curriculum_classification.py` change and belongs to **9U.2-G4**, with 9U.1-C-grade
  review — **not** G0/G1/G2.

---

## 12. Validation checklist for this phase

- [x] The exact 21 target questions are identified (from the import report + inventory total).
- [x] Q91 / Q93 / Q128 recorded as PROTECTED, not curated, not modified.
- [x] No file under `src/` modified.
- [x] No migration modified.
- [x] No database write.
- [x] No provider / OpenAI call.
- [x] No `catalog_node` created.
- [x] No `RetrievalVocabularyEntry` created.
- [x] No classification created.
- [ ] **21 individual fiches completed** — BLOCKED: requires question statement text
      (see §5). To be completed via the §6 protocol by a curator with database access.

---

## 13. Final decision

```
PHASE_9U2_G0_COMPLETE:            NO   (framework ready; fiches pending statement text)
QUESTIONS_ANALYZED:              0 of 21 curated  (21 identified and scoped)
NEW_CONTENT_GROUPS_IDENTIFIED:   NOT_DETERMINABLE without statements
EXISTING_CONTENT_GROUPS:         NOT_DETERMINABLE without statements
HUMAN_REVIEW_GROUPS:             ≥ 1 pre-identified (Q91 historical row; outside the 21)
DATABASE_WRITES:                 0
OPENAI_CALLS:                    0
ALEMBIC_EXECUTION:               0
PRODUCTION_CLASSIFICATIONS_CREATED: 0
FILES_MODIFIED:                  docs/phase9u2_g0_curation.md  (this file only)

FINAL_DECISION:
PHASE_9U2_G0_NEEDS_REVIEW   ← SUPERSEDED. See §15 for the completed curation
                              (FINAL_DECISION: PHASE_9U2_G0_COMPLETE, 2026-09-07).
```

---

## 15. G0-RESUME — COMPLETED CURATION (2026-09-07)

**Source:** `scratchpad_question_text_9u2.txt` — read-only DB extraction
(`QUESTION_TEXT_EXTRACTION_PASS`, 21/21 with text, 0 identity conflicts). Every `EVIDENCE`
is a literal excerpt of that file's `STATEMENT` / `ALTERNATIVAS`. No content inferred from
the question number. **No existing catalog node adequately covers any of the 21.**

### 15.1 — Individual fiches

**#92** — qv `981052a8-846d-41b1-989b-354bdda2f20a`
DISCIPLINE PHYSICS · AREA Termologia · CONTENT Mudanças de estado / calor latente · SUBCONTENT — · CORE_CONCEPT: durante a ebulição a temperatura é constante; mais chama só acelera a evaporação e gasta combustível.
EXISTING_CONTENT NO · EXISTING_NODE_CODE — · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Mudanças de estado físico / calor latente" · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Termologia
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM (overlap com físico-química; concepção decisiva é o patamar de temperatura)
EVIDENCE: "elevar a temperatura de ebulição da água" ; "Ao abaixar o fogo, reduz-se a chama, pois assim evita-se o(a)" ; alt. E "consumo de gás desnecessário".
RATIONALE: a questão pergunta por que reduzir a chama após iniciar a ebulição — porque a temperatura já é fixa, então energia extra só desperdiça gás.
NEXT_STATE NEW_CONTENT_REQUIRED

**#94** — qv `6a47c779-7412-4c50-a697-07a1a36b9a7d`
DISCIPLINE PHYSICS · AREA Ondulatória · CONTENT Fenômenos ondulatórios · SUBCONTENT Interferência (destrutiva) · CORE_CONCEPT: cancelamento de ruído por interferência destrutiva de ondas sonoras.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Fenômenos ondulatórios (interferência)" · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Ondulatória
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "tecnologia redutora de ruído — Cancelamento de Ruído (CR)" ; "baseia-se em qual fenômeno ondulatório?" ; alt. B "Interferência".
RATIONALE: pergunta explicitamente o fenômeno ondulatório do CR — superposição de onda em oposição de fase → interferência destrutiva.
NEXT_STATE NEW_CONTENT_REQUIRED

**#95** — qv `1446af69-0ed8-4046-be8b-d0d616036bd4`
DISCIPLINE CHEMISTRY · AREA Físico-Química (`CHEMISTRY-PHYSICAL` existe) · CONTENT Equilíbrio químico · SUBCONTENT Deslocamento de equilíbrio (Le Chatelier — efeito da temperatura) · CORE_CONCEPT: reação reversível exotérmica; aquecer desloca o equilíbrio revertendo a cor.
EXISTING_CONTENT NO (AREA existe; não há CONTENT de equilíbrio) · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Equilíbrio químico" · PROPOSED_PARENT_NODE `CHEMISTRY-PHYSICAL`
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "sal de cobalto que muda de cor em presença de água, de acordo com a equação química: ∆H < 0 (azul) (rosa)" ; alt. D "Aqueceria com secador de cabelos".
RATIONALE: reação reversível com ∆H declarado; a tarefa é revertê-la mudando a temperatura → Le Chatelier. **Não é cinética.**
NEXT_STATE NEW_CONTENT_REQUIRED

**#103** — qv `85849cf8-ddd1-4aa7-9c1f-6ab514c241fa`
DISCIPLINE CHEMISTRY · AREA Química Geral (nova) · CONTENT Polaridade e forças intermoleculares (solubilidade) · SUBCONTENT "semelhante dissolve semelhante" · CORE_CONCEPT: extração do óleo apolar pelo hexano apolar ocorre por afinidade de polaridade.
EXISTING_CONTENT NO (`CHEMISTRY-SOLUTIONS` = concentração/diluição) · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Polaridade e forças intermoleculares" · PROPOSED_PARENT_NODE CHEMISTRY → (new AREA) Química Geral
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM (Química Geral vs Físico-Química)
EVIDENCE: "Qual das subetapas do processo é realizada em função apenas da polaridade das substâncias?" ; "Extração … Obtenção do óleo bruto com hexano".
RATIONALE: isola a etapa regida apenas pela polaridade — a extração por solvente apolar.
NEXT_STATE NEW_CONTENT_REQUIRED

**#104** — qv `919f1846-8030-40bb-be7d-19d413b96e2d`
DISCIPLINE CHEMISTRY · AREA Físico-Química (`CHEMISTRY-PHYSICAL` existe) · CONTENT Ácidos e bases (pH) · SUBCONTENT acidificação de meio aquoso por CO₂ · CORE_CONCEPT: CO₂ dissolvido forma ácido carbônico e reduz o pH da água.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Ácidos e bases (pH)" · PROPOSED_PARENT_NODE `CHEMISTRY-PHYSICAL`
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "O gás é facilmente solubilizado em água" ; alt. A "redução do potencial hidrogeniônico da água".
RATIONALE: a alternativa correta é literalmente a queda do "potencial hidrogeniônico" (pH); o contexto ecológico é acessório.
NEXT_STATE NEW_CONTENT_REQUIRED

**#105** — qv `ff5056bc-9319-48d2-8afb-fa09e29e990f`
DISCIPLINE BIOLOGY · AREA Fisiologia Animal / Zoologia (nova) · CONTENT Fisiologia e adaptações animais · SUBCONTENT impermeabilização das penas e flutuação · CORE_CONCEPT: a camada de cera das penas retém ar e garante a flutuação; o óleo a remove.
EXISTING_CONTENT NO (`BIOLOGY-CYTOLOGY` apenas) · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Fisiologia e adaptações animais" · PROPOSED_PARENT_NODE BIOLOGY → (new AREA) Fisiologia Animal / Zoologia
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM
EVIDENCE: "precisam recuperar a capacidade de flutuação" ; alt. E "refazer a camada de cera impermeabilizante das penas".
RATIONALE: a resposta nomeia uma estrutura biológica (camada de cera das penas) cuja perda pelo óleo elimina a flutuação.
ALTERNATIVES: poderia ser enquadrada em FÍSICA (empuxo/densidade média) — sinalizado, não escolhido.
NEXT_STATE NEW_CONTENT_REQUIRED

**#107** — qv `03a2095a-4f63-4ce9-9cb3-07863a8f4aa9`  ·  **HUMAN_REVIEW**
DISCIPLINE CHEMISTRY · AREA Química Orgânica (nova) · CONTENT Funções orgânicas e reconhecimento de estruturas · SUBCONTENT HUMAN_REVIEW · CORE_CONCEPT: reconhecer, entre 5 estruturas, a de cadeia poli-insaturada com álcool primário (hidroxila em carbono terminal).
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES (domínio: Química Orgânica) · PROPOSED_CONTENT_NAME "Funções orgânicas e reconhecimento de estruturas" · PROPOSED_PARENT_NODE CHEMISTRY → (new AREA) Química Orgânica
VOCABULARY_STATUS HUMAN_REVIEW · CONFIDENCE LOW (domínio MEDIUM; discriminação estrutural impossível pelo texto)
EVIDENCE: "O principal componente do óleo de rosas tem cadeia poli-insaturada e hidroxila em carbono terminal" ; "estas estruturas químicas: OH CHO OH CHO CHOOH OH CHO OH CHO CHOOH … (1) (2) (3) (4) (5)".
RATIONALE: o domínio (grupos funcionais / insaturação) é claro. As 5 estruturas foram achatadas para uma cadeia de tokens repetidos (`OH CHO OH CHO CHOOH …`) sem correspondência 1↔estrutura.
WHAT CANNOT BE DETERMINED: qual das opções 1–5 é a substância; o subconteúdo exato (álcool vs aldeído — o fluxo de tokens mistura "OH" e "CHO"). **Não adivinhar a alternativa.**
NEXT_STATE HUMAN_REVIEW

**#111** — qv `85476454-711b-4b81-a34b-426c256a7448`
DISCIPLINE CHEMISTRY · AREA Química Ambiental (nova) · CONTENT Poluição atmosférica · SUBCONTENT poluentes da combustão (metais pesados, NOₓ, SOₓ) · CORE_CONCEPT: resíduos da queima de fogos contêm metais pesados e gases tóxicos → poluição atmosférica.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Poluição atmosférica" · PROPOSED_PARENT_NODE CHEMISTRY → (new AREA) Química Ambiental
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "compostos de sódio, potássio, bário, cálcio, chumbo, antimônio, cromo … dióxidos de nitrogênio e enxofre" ; alt. D "os produtos da queima contêm metais pesados e gases tóxicos que resultam em poluição atmosférica".
RATIONALE: alternativa correta e resíduos listados caracterizam item de poluição atmosférica / emissões tóxicas.
NEXT_STATE NEW_CONTENT_REQUIRED

**#112** — qv `76532306-6845-42b9-99e3-c5e5724c70ba`
DISCIPLINE PHYSICS · AREA Eletromagnetismo (nova) · CONTENT Eletrostática — condutores em equilíbrio · SUBCONTENT blindagem eletrostática (gaiola de Faraday) · CORE_CONCEPT: campo elétrico nulo dentro de condutor fechado; a carcaça metálica blinda o interior.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Eletrostática (eletrização e condutores em equilíbrio)" · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Eletromagnetismo
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "Qual o motivo físico da proteção fornecida pelos automóveis" ; alt. C "Blindagem pela carcaça metálica".
RATIONALE: item direto de gaiola de Faraday / blindagem eletrostática.
NEXT_STATE NEW_CONTENT_REQUIRED

**#113** — qv `f1dacdfa-cccc-4453-98ff-ce7d8ea3d0f8`
DISCIPLINE CHEMISTRY · AREA Química Geral (nova) · CONTENT Propriedades da matéria — densidade · SUBCONTENT medição de volume por deslocamento · CORE_CONCEPT: validar as moedas comparando a densidade medida (massa / volume deslocado) com a do cobre (~9 g/cm³).
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Propriedades da matéria (densidade)" · PROPOSED_PARENT_NODE CHEMISTRY → (new AREA) Química Geral
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM (fronteira química/física; a resposta numérica depende de uma figura)
EVIDENCE: "a densidade do cobre metálico é próxima de 9 g cm−³" ; "sequencialmente inseridas em uma proveta contendo 5 mL de água".
RATIONALE: o conceito (densidade = massa/volume, volume por deslocamento) é inequívoco pelo texto. As leituras por moeda vêm de um esquema (`Proveta … 2 A A A A B D 4 6 8 …`) parcialmente corrompido — afeta apenas a resposta numérica.
NEXT_STATE NEW_CONTENT_REQUIRED

**#125** — qv `73bfeec2-df3e-43a7-9ca2-fd592e308ed0`
DISCIPLINE PHYSICS · AREA Eletromagnetismo (nova) · CONTENT Eletrostática — processos de eletrização · SUBCONTENT eletrização por atrito (transferência de elétrons) · CORE_CONCEPT: eletrização por movimentação de elétrons entre corpos, não criação de carga nem troca de átomos.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Eletrostática (eletrização e condutores em equilíbrio)" (compartilhado com #112) · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Eletromagnetismo
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "Por qual motivo ocorre a eletrização ilustrada na tirinha?" ; alt. D "Movimentação de elétrons entre a calça e os pelos do gato".
RATIONALE: modelo de transferência de carga na eletrização por atrito; conceito legível pelas alternativas apesar da tirinha.
NEXT_STATE NEW_CONTENT_REQUIRED

**#126** — qv `73a9eae5-7ce7-4ec2-92c1-19f489849152`
DISCIPLINE CHEMISTRY · AREA Química Orgânica (nova) · CONTENT Polímeros · SUBCONTENT polímeros biodegradáveis vs convencionais · CORE_CONCEPT: a vantagem do polímero biodegradável é a decomposição em tempo muito menor.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Polímeros" · PROPOSED_PARENT_NODE CHEMISTRY → (new AREA) Química Orgânica
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "substituindo polímeros convencionais por polímeros biodegradáveis" ; alt. D "são degradados em um tempo bastante menor que os convencionais".
RATIONALE: item de química de polímeros / biodegradabilidade.
NEXT_STATE NEW_CONTENT_REQUIRED

**#127** — qv `4b923779-a748-4ebf-9342-241f8c71bf84`
DISCIPLINE BIOLOGY · AREA Ecologia (nova) · CONTENT Ecologia de populações e conservação · SUBCONTENT fragmentação de habitats / corredores ecológicos · CORE_CONCEPT: ligar fragmentos com corredores mitiga o isolamento populacional e conserva a biodiversidade.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Ecologia de populações e conservação" · PROPOSED_PARENT_NODE BIOLOGY → (new AREA) Ecologia
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "A fragmentação dos hábitats é caracterizada pela formação de ilhas da paisagem original" ; alt. C "construção de corredores ecológicos".
RATIONALE: fragmentação de habitat, efeito de borda, isolamento populacional e estratégia de conservação.
NEXT_STATE NEW_CONTENT_REQUIRED

**#129** — qv `6dec6d14-911b-42f5-bdb8-43f2c6a3242a`
DISCIPLINE BIOLOGY · AREA Ecologia (nova) · CONTENT Ciclos biogeoquímicos · SUBCONTENT ciclo do carbono / combustíveis fósseis · CORE_CONCEPT: queimar petróleo libera carbono há muito aprisionado nos sedimentos, desequilibrando o ciclo do carbono.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Ciclos biogeoquímicos" · PROPOSED_PARENT_NODE BIOLOGY → (new AREA) Ecologia
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "acarreta para o ambiente um desequilíbrio no ciclo do" ; alt. D "carbono, devido à liberação das cadeias carbônicas aprisionadas abaixo dos sedimentos".
RATIONALE: nomeia explicitamente o ciclo do carbono; liberação de carbono fóssil.
NEXT_STATE NEW_CONTENT_REQUIRED

**#130** — qv `2b5dee92-89b1-419b-91a0-553eb578cfb5`
DISCIPLINE PHYSICS · AREA Eletromagnetismo (nova) · CONTENT Indução eletromagnética · SUBCONTENT Lei de Faraday–Lenz · CORE_CONCEPT: a fem induzida cresce quando o fluxo magnético varia mais rapidamente (maior velocidade angular).
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Indução eletromagnética" · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Eletromagnetismo
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "produz um fluxo magnético variável através delas, gerando uma diferença de potencial" ; alt. D "rapidez com que o fluxo magnético varia através das bobinas".
RATIONALE: item direto de lei de Faraday num gerador — fem proporcional à taxa de variação do fluxo.
NEXT_STATE NEW_CONTENT_REQUIRED

**#133** — qv `289fc64a-dbda-4c5c-b942-8bfd132990ae`
DISCIPLINE PHYSICS · AREA Termologia (nova) · CONTENT Termodinâmica / máquinas térmicas · SUBCONTENT ciclo de refrigeração — trocas de calor no condensador e trabalho do compressor · CORE_CONCEPT: ambiente mais quente dificulta a liquefação no condensador, exigindo mais trabalho do compressor e mais energia.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Termodinâmica e máquinas térmicas" · PROPOSED_PARENT_NODE PHYSICS → (new AREA) Termologia
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM
EVIDENCE: "não deve ser instalado próximo a fontes de calor" ; alt. D "a liquefação da substância refrigerante no condensador exige mais trabalho do compressor".
RATIONALE: ciclo de refrigeração / rejeição de calor no condensador / trabalho do compressor — termodinâmica de máquinas térmicas.
NEXT_STATE NEW_CONTENT_REQUIRED

**#134** — qv `406e3e57-9753-4051-8ecf-69ee36e6d038`
DISCIPLINE PHYSICS · AREA Mecânica (`PHYSICS-MECHANICS` existe) · CONTENT Hidrostática — pressão em fluidos · SUBCONTENT Teorema de Stevin + leitura de gráfico · CORE_CONCEPT: calcular a variação de pressão hidrostática na profundidade e obter, no gráfico, o tempo de descompressão.
EXISTING_CONTENT NO (AREA existe; só há CONTENT de cinemática) · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Hidrostática (pressão em fluidos)" · PROPOSED_PARENT_NODE `PHYSICS-MECHANICS`
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM (resposta numérica depende do gráfico parcialmente corrompido; o conceito é claro por ρ e g dados)
EVIDENCE: "a densidade da água seja de ρ = 1 000 kg m −3" ; "a aceleração da gravidade seja igual a 10 m s −2" ; "processo de descompressão".
RATIONALE: ρ e g são fornecidos precisamente para o cálculo da pressão hidrostática (Stevin) antes da leitura do gráfico.
NEXT_STATE NEW_CONTENT_REQUIRED

**#135** — qv `e489f8ab-1138-4a5c-b86d-1b56d20e45d7`
DISCIPLINE PHYSICS · AREA Mecânica / Estática (`PHYSICS-MECHANICS` existe) · CONTENT Pressão (força/área) e semelhança geométrica (escala) · SUBCONTENT — · CORE_CONCEPT: com o mesmo material e semelhança geométrica, a pressão na base escala com o fator linear (P ∝ V/A ∝ L) → razão 100 = 10².
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Pressão e análise de escala" · PROPOSED_PARENT_NODE `PHYSICS-MECHANICS`
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM
EVIDENCE: "protótipo dessa torre em escala 1:100, usando os mesmos materiais" ; "qual é a razão entre as pressões (Ptorre)/(Pmodelo)?".
RATIONALE: enunciado em termos de massa e pressão "na fundação"; o raciocínio é como a pressão escala sob semelhança geométrica.
ALTERNATIVES: poderia ser MATEMÁTICA (proporcionalidade / potências de 10) — sinalizado, não escolhido (as grandezas físicas dominam o enunciado).
NEXT_STATE NEW_CONTENT_REQUIRED

**#152** — qv `d1b383da-c572-4a9f-9d29-481850df251f`
DISCIPLINE MATH · AREA Grandezas e Medidas (nova) · CONTENT Área de figuras planas e aplicações · SUBCONTENT revestimento + otimização de compra/custo · CORE_CONCEPT: calcular quantas peças cobrem a área da sala (aproveitando recortes) e escolher a combinação de caixas de menor sobra e menor preço.
EXISTING_CONTENT NO (`MATH-ALGEBRA-*` inadequado) · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Área de figuras planas e aplicações" · PROPOSED_PARENT_NODE MATH → (new AREA) Grandezas e Medidas
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE MEDIUM
EVIDENCE: "sala tem formato retangular com 3,2 m de largura e 3,6 m de comprimento" ; "peças do porcelanato têm formato de um quadrado com lado medindo 80 cm" ; "menor sobra de pisos e resulta no menor preço".
RATIONALE: núcleo é área de retângulo × peça quadrada com aproveitamento de recortes, seguido de comparação de custo — Grandezas e Medidas, não Álgebra.
NEXT_STATE NEW_CONTENT_REQUIRED

**#153** — qv `5e33344f-0908-4085-b337-13627153b965`
DISCIPLINE MATH · AREA Análise Combinatória (nova) · CONTENT Princípio fundamental da contagem · SUBCONTENT contagem de ocorrências de um algarismo num intervalo · CORE_CONCEPT: contar quantas vezes o algarismo 2 aparece nas numerações 100–399.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Princípio fundamental da contagem" · PROPOSED_PARENT_NODE MATH → (new AREA) Análise Combinatória
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "Os quartos serão numerados de 100 a 399" ; "Qual a quantidade mínima de peças, simbolizando o algarismo 2".
RATIONALE: contagem de ocorrências de dígito num intervalo fixo — contagem combinatória.
NEXT_STATE NEW_CONTENT_REQUIRED

**#172** — qv `d786f5cc-f52c-4192-96eb-a66a58b3875f`
DISCIPLINE MATH · AREA Estatística (nova) · CONTENT Medidas de tendência central — média aritmética · SUBCONTENT manipulação de média sob substituição de elementos · CORE_CONCEPT: determinar a média mínima dos 3 novos jogadores para a média da equipe atingir o alvo, dadas as substituições e a contratação conhecida.
EXISTING_CONTENT NO · NEW_CONTENT_REQUIRED YES · PROPOSED_CONTENT_NAME "Medidas de tendência central (média)" · PROPOSED_PARENT_NODE MATH → (new AREA) Estatística
VOCABULARY_STATUS NEW_VOCABULARY_REQUIRED · CONFIDENCE HIGH
EVIDENCE: "aumentar a estatura média de sua equipe de 1,93 m para, no mínimo, 1,99 m" ; "Qual deverá ser a média mínima das estaturas … para o grupo de três novos jogadores".
RATIONALE: raciocínio de média aritmética com substituição de elementos — item de estatística / tendência central.
NEXT_STATE NEW_CONTENT_REQUIRED

### 15.2 — Groups

- **GROUP A — EXISTING CATALOG CONTENT — count 0.** Nenhuma das 21 mapeia para nó existente.
- **GROUP B — NEW CONTENT REQUIRED — count 20:** `92, 94, 95, 103, 104, 105, 111, 112, 113, 125, 126, 127, 129, 130, 133, 134, 135, 152, 153, 172`.
- **GROUP C — EXISTING CONTENT + NEW VOCABULARY — count 0.** N/A.
- **GROUP D — HUMAN REVIEW — count 1:** `107` (estruturas químicas perdidas na extração; domínio determinável, alternativa não).
- **GROUP E — OUT OF SCOPE / BLOCKED — count 0.**
- Questões que compartilham um mesmo vocabulário: `112 + 125` (Eletrostática). `127 + 129` compartilham a nova AREA "Ecologia" com contents distintos; `92 + 133` a nova AREA "Termologia" com contents distintos.

### 15.3 — Proposed new AREAs (proposals only — nothing created)

| PROPOSED_AREA (code suggestion) | PARENT_DISCIPLINE | QUESTIONS | CONFIDENCE |
|---|---|---|---|
| Química Geral (`CHEMISTRY-GENERAL`) | CHEMISTRY | 103, 113 | MEDIUM |
| Química Orgânica (`CHEMISTRY-ORGANIC`) | CHEMISTRY | 107, 126 | HIGH |
| Química Ambiental (`CHEMISTRY-ENVIRONMENTAL`) | CHEMISTRY | 111 | MEDIUM |
| Termologia (`PHYSICS-THERMAL`) | PHYSICS | 92, 133 | HIGH |
| Ondulatória (`PHYSICS-WAVES`) | PHYSICS | 94 | HIGH |
| Eletromagnetismo (`PHYSICS-ELECTROMAGNETISM`) | PHYSICS | 112, 125, 130 | HIGH |
| Ecologia (`BIOLOGY-ECOLOGY`) | BIOLOGY | 127, 129 | HIGH |
| Fisiologia Animal / Zoologia (`BIOLOGY-ANIMAL-PHYSIOLOGY`) | BIOLOGY | 105 | MEDIUM |
| Grandezas e Medidas (`MATH-MEASUREMENT`) | MATH | 152 | MEDIUM |
| Análise Combinatória (`MATH-COMBINATORICS`) | MATH | 153 | HIGH |
| Estatística (`MATH-STATISTICS`) | MATH | 172 | HIGH |

Reused existing AREAs: `CHEMISTRY-PHYSICAL` (95, 104), `PHYSICS-MECHANICS` (134, 135).

### 15.4 — Proposed new CONTENTS (one node per pedagogical concept — proposals only)

| # | DISCIPLINE | PROPOSED_CONTENT_CODE (suggestion) | PARENT_NODE | QUESTIONS | CONF |
|---|---|---|---|---|---|
| N01 | CHEMISTRY | `CHEMISTRY-PHYSICAL-EQUILIBRIUM` | `CHEMISTRY-PHYSICAL` | 95 | HIGH |
| N02 | CHEMISTRY | `CHEMISTRY-PHYSICAL-ACID-BASE` | `CHEMISTRY-PHYSICAL` | 104 | HIGH |
| N03 | CHEMISTRY | `CHEMISTRY-GENERAL-POLARITY-IMF` | `CHEMISTRY-GENERAL` (new) | 103 | MED |
| N04 | CHEMISTRY | `CHEMISTRY-GENERAL-MATTER-PROPERTIES` | `CHEMISTRY-GENERAL` (new) | 113 | MED |
| N05 | CHEMISTRY | `CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS` | `CHEMISTRY-ORGANIC` (new) | 107 *(HUMAN_REVIEW)* | MED |
| N06 | CHEMISTRY | `CHEMISTRY-ORGANIC-POLYMERS` | `CHEMISTRY-ORGANIC` (new) | 126 | HIGH |
| N07 | CHEMISTRY | `CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION` | `CHEMISTRY-ENVIRONMENTAL` (new) | 111 | HIGH |
| N08 | PHYSICS | `PHYSICS-THERMAL-PHASE-CHANGE` | `PHYSICS-THERMAL` (new) | 92 | MED |
| N09 | PHYSICS | `PHYSICS-THERMAL-THERMODYNAMICS` | `PHYSICS-THERMAL` (new) | 133 | MED |
| N10 | PHYSICS | `PHYSICS-WAVES-PHENOMENA` | `PHYSICS-WAVES` (new) | 94 | HIGH |
| N11 | PHYSICS | `PHYSICS-EM-ELECTROSTATICS` | `PHYSICS-ELECTROMAGNETISM` (new) | 112, 125 | HIGH |
| N12 | PHYSICS | `PHYSICS-EM-INDUCTION` | `PHYSICS-ELECTROMAGNETISM` (new) | 130 | HIGH |
| N13 | PHYSICS | `PHYSICS-MECHANICS-HYDROSTATICS` | `PHYSICS-MECHANICS` | 134 | MED |
| N14 | PHYSICS | `PHYSICS-MECHANICS-PRESSURE-SCALE` | `PHYSICS-MECHANICS` | 135 | MED |
| N15 | BIOLOGY | `BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION` | `BIOLOGY-ECOLOGY` (new) | 127 | HIGH |
| N16 | BIOLOGY | `BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES` | `BIOLOGY-ECOLOGY` (new) | 129 | HIGH |
| N17 | BIOLOGY | `BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS` | `BIOLOGY-ANIMAL-PHYSIOLOGY` (new) | 105 | MED |
| N18 | MATH | `MATH-MEASUREMENT-PLANE-AREA` | `MATH-MEASUREMENT` (new) | 152 | MED |
| N19 | MATH | `MATH-COMBINATORICS-COUNTING` | `MATH-COMBINATORICS` (new) | 153 | HIGH |
| N20 | MATH | `MATH-STATISTICS-CENTRAL-TENDENCY` | `MATH-STATISTICS` (new) | 172 | HIGH |

### 15.5 — Proposed new SUBCONTENTS

| CONTENT | SUBCONTENT_REQUIRED | PROPOSED_SUBCONTENT | QUESTIONS |
|---|---|---|---|
| N01 Equilíbrio químico | YES | Deslocamento de equilíbrio (Le Chatelier) | 95 |
| N11 Eletrostática | YES | Processos de eletrização (125) · Condutores em equilíbrio / blindagem (112) | 112, 125 |
| todos os demais | NO | — | — |

### 15.6 — Proposed new VOCABULARIES (design proposals — NOT created)

Proposed `taxonomy_version` = **one** new catalog-generation tag (e.g. `curriculum-v2`),
not one tag per content (see §14/§11). Terms drawn from the extracted text.

| CONTENT | CANONICAL CODE (proposed) | PRIMARY_TERMS | SPECIFIC_TERMS | CONTEXTUAL_TERMS | Q |
|---|---|---|---|---|---|
| N01 | `CHEMISTRY-PHYSICAL-EQUILIBRIUM` | "equilíbrio químico", "deslocamento de equilíbrio", "princípio de Le Chatelier" | "reação reversível", "sentido direto", "sentido inverso", "efeito da temperatura no equilíbrio" | "reação reversível com ∆H", "mudança de cor reversível" | 95 |
| N02 | `CHEMISTRY-PHYSICAL-ACID-BASE` | "ácido carbônico", "potencial hidrogeniônico", "acidificação" | "pH", "H₂CO₃", "CO₂ dissolvido em água", "caráter ácido" | "redução do pH da água", "acidificação dos oceanos" | 104 |
| N03 | `CHEMISTRY-GENERAL-POLARITY-IMF` | "polaridade", "forças intermoleculares", "semelhante dissolve semelhante" | "molécula apolar", "solvente apolar", "hexano", "afinidade" | "extração com solvente apolar", "solubilidade em função da polaridade" | 103 |
| N04 | `CHEMISTRY-GENERAL-MATTER-PROPERTIES` | "densidade", "massa específica" | "g/cm³", "volume deslocado", "proveta", "massa e volume" | "medição de densidade por deslocamento de água" | 113 |
| N05 | `CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS` | "função orgânica", "grupo funcional", "insaturação" | "hidroxila", "álcool primário", "cadeia poli-insaturada", "carbono terminal" | "reconhecimento de estrutura orgânica" | 107 *(HUMAN_REVIEW)* |
| N06 | `CHEMISTRY-ORGANIC-POLYMERS` | "polímero", "polímeros biodegradáveis" | "monômero", "degradação", "polímeros convencionais", "biodegradabilidade" | "substituição de polímeros convencionais por biodegradáveis" | 126 |
| N07 | `CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION` | "poluição atmosférica", "material particulado", "metais pesados" | "dióxido de nitrogênio", "dióxido de enxofre", "gases tóxicos", "chumbo" | "resíduos da combustão suspensos no ar" | 111 |
| N08 | `PHYSICS-THERMAL-PHASE-CHANGE` | "calor latente", "mudança de estado", "ebulição" | "temperatura de ebulição", "vaporização", "patamar de temperatura", "evaporação" | "temperatura constante durante a mudança de fase" | 92 |
| N09 | `PHYSICS-THERMAL-THERMODYNAMICS` | "termodinâmica", "máquina térmica", "ciclo de refrigeração" | "condensador", "compressor", "trabalho do compressor", "liquefação do fluido refrigerante" | "troca de calor com o ambiente" | 133 |
| N10 | `PHYSICS-WAVES-PHENOMENA` | "fenômeno ondulatório", "interferência", "interferência destrutiva" | "onda sonora", "superposição de ondas", "cancelamento de ruído" | "supressão de ruído por ondas em oposição de fase" | 94 |
| N11 | `PHYSICS-EM-ELECTROSTATICS` | "eletrostática", "eletrização", "carga elétrica" | "eletrização por atrito", "transferência de elétrons", "gaiola de Faraday", "blindagem eletrostática", "condutor em equilíbrio" | "movimentação de elétrons entre os corpos", "campo elétrico nulo no interior do condutor" | 112, 125 |
| N12 | `PHYSICS-EM-INDUCTION` | "indução eletromagnética", "lei de Faraday", "fluxo magnético" | "fluxo magnético variável", "força eletromotriz induzida", "bobina", "velocidade angular" | "diferença de potencial induzida por variação de fluxo" | 130 |
| N13 | `PHYSICS-MECHANICS-HYDROSTATICS` | "hidrostática", "pressão hidrostática", "teorema de Stevin" | "pressão em fluidos", "profundidade", "ρ g h", "densidade do fluido" | "variação de pressão com a profundidade" | 134 |
| N14 | `PHYSICS-MECHANICS-PRESSURE-SCALE` | "pressão", "força por unidade de área", "semelhança geométrica" | "escala", "fator de escala linear", "razão entre pressões", "mesmo material" | "como a pressão na base varia com a escala do modelo" | 135 |
| N15 | `BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION` | "fragmentação de habitat", "conservação da biodiversidade", "corredor ecológico" | "efeito de borda", "isolamento de população", "ilha de habitat", "extinção local" | "ligar fragmentos para conservar espécies" | 127 |
| N16 | `BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES` | "ciclo biogeoquímico", "ciclo do carbono" | "combustível fóssil", "carbono aprisionado nos sedimentos", "queima de combustíveis" | "desequilíbrio no ciclo do carbono pela queima de petróleo" | 129 |
| N17 | `BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS` | "adaptação animal", "impermeabilização das penas" | "camada de cera", "flutuação de aves aquáticas", "glândula uropigiana" | "recuperar a capacidade de flutuação após contato com óleo" | 105 |
| N18 | `MATH-MEASUREMENT-PLANE-AREA` | "área", "figura plana", "revestimento" | "retângulo", "quadrado", "lado", "aproveitamento de recortes", "menor sobra", "menor preço" | "quantas peças para revestir a sala", "compra de menor custo" | 152 |
| N19 | `MATH-COMBINATORICS-COUNTING` | "contagem", "princípio fundamental da contagem" | "algarismo", "ocorrências de um dígito", "intervalo de números" | "quantas vezes o algarismo 2 aparece de 100 a 399" | 153 |
| N20 | `MATH-STATISTICS-CENTRAL-TENDENCY` | "média aritmética", "média das estaturas" | "medida de tendência central", "substituição de elementos", "valor mínimo da média" | "qual média os novos elementos devem ter para a média do conjunto atingir o alvo" | 172 |

### 15.7 — Coverage matrix

```
TOTAL_QUESTIONS: 21
EXACT_EXISTING_MATCH:              0
EXISTING_CONTENT + NEW_VOCABULARY: 0
NEW_CONTENT_REQUIRED:            20  (92,94,95,103,104,105,111,112,113,125,126,127,129,130,133,134,135,152,153,172)
HUMAN_REVIEW:                     1  (107)
OUT_OF_SCOPE:                     0
BLOCKED:                          0
READY_FOR_INITIAL:               0
SUM = 20 + 1 = 21  ✓

CONFIDENCE:  HIGH 12 (94,95,104,111,112,125,126,127,129,130,153,172) · MEDIUM 8 (92,103,105,113,133,134,135,152; 105/135 carry a flagged cross-discipline alternative) · LOW 1 (107)
CATALOG_COVERAGE (existing nodes):  0 / 21  (0%)
VOCABULARY_COVERAGE (existing vocab): 0 / 21  (0%)  — nenhuma das 21 é cinética química
NEW AREAS: 11 · NEW CONTENTS: 20 · NEW VOCABULARIES: 20
```

### 15.8 — Validation

- [x] 21/21 target questions analysed.
- [x] Every fiche has a literal `EVIDENCE` excerpt from `scratchpad_question_text_9u2.txt`.
- [x] No content inferred from the question number.
- [x] Q107 explicitly handled — domain classified, structural discrimination → HUMAN_REVIEW, no option guessed.
- [x] Q152 / Q153 / Q172 individually analysed (Grandezas e Medidas / Contagem / Estatística — not all "Funções").
- [x] No question mapped to `CHEMISTRY-PHYSICAL-KINETICS`.
- [x] Q91 / Q93 / Q128 untouched; absent from every proposal.
- [x] 0 DB writes · 0 OpenAI/provider · 0 Alembic · 0 Batch-0.
- [x] 0 catalog nodes created · 0 vocabularies created · 0 classifications created.
- [x] Only `docs/phase9u2_g0_curation.md` modified; §0–§14 history preserved.
- [x] Coverage matrix sums to 21.

### 15.9 — Final decision

```
PHASE_9U2_G0_COMPLETE:            YES
TOTAL_ANALYZED:                   21 / 21
QUESTIONS_WITH_EVIDENCE:          21 / 21
QUESTIONS_HIGH_CONFIDENCE:        12
QUESTIONS_MEDIUM_CONFIDENCE:       8
QUESTIONS_LOW_CONFIDENCE:          1  (107)
EXISTING_CONTENT_GROUPS:           0
NEW_CONTENT_GROUPS:               20  (N01–N20)
NEW_VOCABULARIES_REQUIRED:        20
HUMAN_REVIEW:                      1  (107)
OUT_OF_SCOPE:                      0
READY_FOR_INITIAL:                0
Q107_STATUS: HUMAN_REVIEW (domínio Química Orgânica; qual estrutura = qual opção não determinável)
Q152_STATUS: NEW_CONTENT_REQUIRED — MATH / Grandezas e Medidas / área e aplicações
Q153_STATUS: NEW_CONTENT_REQUIRED — MATH / Análise Combinatória / contagem
Q172_STATUS: NEW_CONTENT_REQUIRED — MATH / Estatística / média aritmética
PROTECTED_QUESTIONS: Q91, Q93, Q128 — untouched
DATABASE_WRITES: 0 · OPENAI_CALLS: 0 · ALEMBIC_EXECUTION: 0
CATALOG_NODES_CREATED: 0 · VOCABULARIES_CREATED: 0 · CLASSIFICATIONS_CREATED: 0
FILES_MODIFIED: docs/phase9u2_g0_curation.md

FINAL_DECISION:
PHASE_9U2_G0_COMPLETE
```
