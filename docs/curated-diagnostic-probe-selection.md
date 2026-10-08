# Seleção determinística do instrumento de sondagem

Documento do bloco **CURATED_DIAGNOSTIC_PROBE_SELECTION_V1** (2026-10-07),
sobre `fase6/vetorial`.

---

## 1. O P0, e a causa raiz

Medido no navegador em 2026-10-07: a sondagem de Estequiometria serviu três
itens, e **um veio do banco genérico, com cinco alternativas** — existindo
cinco itens curados publicados, um por micro-habilidade.

Não era bug. Era ausência.

```
MicroDiagnosticService.start()
  └─ create_practice(content_code=..., question_count=3)
       └─ QuestionBankFilters(content_code=...)     ← filtra por CONTEÚDO
       └─ policy.rank()                             ← ordena por número oficial
       └─ policy.choose(count=3)                    ← os três primeiros
```

A micro-habilidade nunca entrava na pergunta. Os cinco curados competiam em
pé de igualdade com as 20 questões comuns de Estequiometria, e perdiam por
número oficial. Que algum deles aparecesse era sorte.

---

## 2. O que é um *curated diagnostic probe*

Um item **publicado e validado por humano, com finalidade de sondagem
declarada, classificado na micro-habilidade que se quer investigar**.

### O contrato já existia no modelo

O publicador gravava, desde a publicação dos cinco itens:

```python
PedagogicalClassification.metadata_["purpose"] = "PROBE"
```

E **ninguém lia**. Zero migration foi necessária.

### Por que `purpose`, e não `reasoning_type`

Medido no banco de desenvolvimento:

| campo | ocorrências | o que são |
|---|---|---|
| `metadata["purpose"] = "PROBE"` | **5** | exatamente os curados |
| `reasoning_type = "DIAGNOSTIC"` | **39** | os 5 + 34 do banco gerado por IA |

`reasoning_type` era o candidato natural e teria promovido 34 itens
`AI_VERIFIED` a instrumento deliberado do Núcleo.

### Por que não o prefixo `SOND-`

Código de questão serve a auditoria humana. Como contrato, esconderia
semântica numa string e faria o segundo conteúdo custar o mesmo trabalho que
o primeiro. `tests/test_instrumento_de_sondagem.py` varre o **código** do
módulo (sem docstrings) atrás de `SOND-`, de nome de micro-habilidade e de
nome de conteúdo.

### Os critérios, todos necessários

| critério | por quê |
|---|---|
| mede a habilidade pedida, no conteúdo pedido | servir outra registra a lacuna errada |
| `purpose = PROBE` | é a finalidade declarada |
| `lifecycle = ACTIVE` | classificação aposentada não qualifica |
| `provenance = HUMAN_VALIDATED` | gerado por IA não é instrumento deliberado |
| `validated_by_external_identity` preenchido | "um humano validou" sem nome não é rastreável |
| `status = PUBLISHED` | rascunho não chega ao aluno |
| escopo acessível | `PUBLIC`, ou `SCHOOL` da própria escola |
| gabarito definido | sem ele a resposta não é conferível |
| sem dependência visual, não protegido | o aluno precisa poder responder |

**Falha fechado.** 13 defeitos testados, cada um desqualificando, cada um
devolvendo o motivo em texto — porque é o motivo que torna a decisão
auditável: "não havia curado" e "havia um, em rascunho" são situações
diferentes, e a segunda é erro de publicação que alguém precisa ver.

---

## 3. Finalidade não é formato

`purpose` diz **para que** o item existe. `question_type` diz **como** se
responde. São dimensões independentes, e o contrato só conhece a primeira.

O candidato declara `gabarito_definido: bool`, e não uma contagem de
alternativas. Quem carrega do banco decide o que isso significa no formato
dele — hoje, "exatamente uma alternativa válida".

A próxima evolução do produto é uma sondagem conversacional. Se o contrato
exigisse alternativas, ela nasceria tendo de mentir sobre o próprio formato.
Há teste de que o módulo não menciona `multiple_choice`, `question_type`,
`alternativa`, `option_key` nem `is_valid_option`.

**Esta arquitetura foi preparada para isso, e a sondagem conversacional NÃO
foi implementada neste bloco.**

---

## 4. Quem decide o quê

```
Pedagogical Knowledge Graph   organiza o conhecimento
  └─ plano_de_sondagem        QUAIS micro-habilidades investigar
       └─ seletor_de_sondagem QUAL instrumento para cada uma
            └─ Question Bank  fornece os instrumentos
                 └─ Evidence Engine  interpreta as respostas
```

Nenhuma dessas responsabilidades se mistura.

### A ordem é do grafo

Da base ao topo, por profundidade — a mesma noção que `primeiro_gargalo` já
usa. Começar pelo topo da cadeia faz o aluno errar por travar no primeiro
elo, e o sistema registra a lacuna errada.

### O filtro é da realidade

Estequiometria declara nove micro-habilidades; duas — `CONCEITO_DE_MOL` e
`LEITURA_DE_COEFICIENTE` — não têm **nenhuma** questão classificada no
acervo. O plano as pula.

Isso **não** é o acervo decidindo pedagogia: a ordem continua sendo do grafo,
e o acervo só responde onde existe com que medir. Há teste de que tirar uma
habilidade do acervo **encurta** o plano sem **reordenar** o resto. O que o
sistema recusa é perguntar algo cuja resposta ele não saberia interpretar.

As puladas viajam em `skills_without_instrument` — falta de instrumento é
informação, não detalhe silencioso.

---

## 5. A política de seleção

Para cada micro-habilidade do plano:

1. **CURADO** — se houver item elegível, ele vence, sempre.
2. **FALLBACK** — senão, questão do acervo classificada na **mesma**
   habilidade, publicada, conferível e acessível.
3. **Nada** — a habilidade é omitida. Servir item de outra habilidade
   registraria a lacuna errada.

### Desempate

Número oficial, depois o `question_version_id`. Hoje há um curado por
habilidade e nenhum empate acontece — a política existe assim mesmo, porque
"funciona porque só há um" não é política: no dia em que houver dois, o aluno
não pode ver o instrumento mudar sozinho entre duas aberturas da mesma tela.

Um mesmo item nunca é servido duas vezes na mesma sondagem.

---

## 6. Observabilidade

`selection["probe"]`, como **dado** no payload — não log, não texto de tela:

```json
{
  "mode": "PER_SKILL",
  "instruments": [
    {"skill": "LEITURA_DE_FORMULA",
     "question_version_id": "...",
     "origin": "CURATED",
     "reason": "item curado do Núcleo para esta micro-habilidade: ..."}
  ],
  "requested_skills": [...],
  "skills_without_instrument": ["CONCEITO_DE_MOL", "LEITURA_DE_COEFICIENTE"]
}
```

E `selection["selection_mode"]` diz se os ids vieram do chamador
(`CALLER_SUPPLIED`) ou do ranking por conteúdo (`CONTENT_RANKED`).

---

## 7. Sondar não é medir

Este bloco **não alterou** a política de evidência. Continuam valendo:

- um erro na sondagem pode gerar `SUSPECTED_GAP`; um acerto **não** confirma
  domínio;
- `min_sample_size` inalterado;
- evidência medida vence suspeita;
- selecionar e servir um instrumento **não** cria mastery.

Medido no caminho real: abrir a sondagem duas vezes deixa
`domain_content_mastery` e `activity_results` em zero. Depois de responder,
`origin_breakdown = {MICRO_DIAGNOSTIC: 3}` — nada mais.

---

## 8. Conteúdo sem grafo não muda

`_instrumentos` devolve `None` e `create_practice` seleciona por conteúdo,
exatamente como antes. É a mesma regra de convivência que
`grafos_pedagogicos` já estabelece: hoje **um** conteúdo dos 37 do catálogo
tem contrato V2.

E há um fail-safe: se a seleção por habilidade não montar o tamanho pedido —
acervo incompleto —, cai na seleção por conteúdo e o relatório diz que foi
isso. Uma sondagem pior é melhor que nenhuma, mas a lacuna precisa aparecer.

---

## 9. Limitações reais

1. **"Bom domínio" com um gargalo aberto.** Respondendo 2 de 3 (errando só
   massa molar), a tela do diagnóstico diz *"Muito bem! Você demonstrou um
   bom domínio de Estequiometria"* e o passo seguinte é investigar massa
   molar. As duas frases estão certas sob as suas próprias regras — a
   política concluiu `PROCEED` com 0,667, e a assimetria diagnóstica
   suspeitou da habilidade errada —, mas juntas na tela se contradizem.
   É **anterior** a este bloco; o que mudou é que agora a sondagem é
   discriminativa e isso ficou mais visível e mais consequente. Não foi
   corrigido aqui porque §7 do bloco proíbe mexer na política de evidência.
   **Candidato a próximo P0.**

2. **A proporção é sondada sem o pré-requisito dela.**
   `PROPORCAO_ESTEQUIOMETRICA` depende de `LEITURA_DE_COEFICIENTE`, que não
   tem instrumento nenhum. Se o aluno errar a proporção, o grafo apontará a
   proporção quando o problema pode estar um degrau abaixo.

3. **Duas das nove micro-habilidades não têm instrumento.** `CONCEITO_DE_MOL`
   e `LEITURA_DE_COEFICIENTE`. O sistema as reporta, mas não as mede.

4. **Um conteúdo.** Só Estequiometria tem grafo. Acrescentar o segundo é
   acrescentar uma linha em `grafos_pedagogicos` mais o módulo do grafo.

5. **O fallback nunca foi exercido em produção.** Não há, hoje, micro-skill
   do piloto com genérico mas sem curado. O caminho está testado em unidade e
   em integração, não observado no navegador.

6. **A listagem do Question Bank não devolve a micro-habilidade.**
   `CurriculumClassificationView.subcontent_code` vem `None` porque a view
   resolve o subconteúdo contra o **catálogo**, e micro-habilidade não é nó do
   currículo. O seletor contorna com consulta própria. O read model do banco
   continua sem expor finalidade pedagógica — quem quiser filtrar probes pela
   UI do Question Bank ainda não consegue.
