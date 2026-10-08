# Piloto Zero — o ciclo fechado e a UX do teste humano

**2026-10-04.** Os oito achados do teste manual, mais um que apareceu no meio.

---

## 1. Os achados, classificados

| # | observado | classe | estado |
|---|---|---|---|
| A | fórmulas sem subscrito numa questão | **bug de apresentação** | corrigido |
| B | fórmulas corretas em outra | — (era o contraste de A) | — |
| C | feedback pouco pedagógico | **bug funcional** (regra no cliente) | corrigido |
| D | resultado sem próximo passo claro | **bug funcional** | corrigido |
| E | campo de texto sem ação de envio | **bug funcional** | corrigido |
| F | Meu Progresso real porém pobre | **dívida de produto** | corrigido |
| G | pouca orientação de navegação | **dívida de produto** | corrigido em parte |
| H | aluno preso nas mesmas 3 perguntas | **bug funcional** — o mais grave | corrigido |
| **I** | **9 de 14 gabaritos em "B"** | **bug funcional**, achado agora | corrigido |

### Por que H era o mais grave (e I, pior ainda)

**H.** O aluno errava as três perguntas, o backend decidia
`PREPARE_PREREQUISITE` — corretamente — e a prontidão da atividade continuava
`DIAGNOSTIC` apontando para o **mesmo** conteúdo. "Continuar" abria outro
microdiagnóstico de Balanceamento. E outro.

O microdiagnóstico **coleta evidência para decidir**. Depois que decidiu,
repeti-lo não acrescenta nada: o que falta é estudar.

**I** apareceu enquanto eu conferia o Caminho A. O bloco anterior mediu o viés
posicional nos itens *candidatos* e corrigiu ali — mas a correção ficou no
pipeline de geração, e o arquivo efetivamente carregado é anterior a ela.

Num teste comum, viés posicional infla uma nota. Num **diagnóstico** produz
**falso-pronto**: quem marcasse "B" em tudo acertava 64%, a política concluía
domínio, e o sistema o liberava para Estequiometria sem saber balancear uma
equação.

| | A | B | C | D | E |
|---|---:|---:|---:|---:|---:|
| antes | 0% | **64%** | 29% | 0% | 7% |
| depois | 21% | 21% | 21% | 21% | 14% |

O teste que trava isso não mede a distribuição por estética: ele roda a
política real e exige que **marcar sempre a mesma letra não libere o aluno**.

## 2. A distinção que faltava

```
sem evidência sobre a base      → DIAGNOSTICAR  (ainda não sei)
evidência, e a base está fraca  → PRATICAR      (já sei, e falta)
evidência, e a base está boa    → deixa de ser obstáculo
```

Isso virou `services/proximo_passo.py`. **Nenhum corte novo mora lá** — as
três faixas vêm de `PerformanceThresholdPolicy`, e há teste lendo a AST do
arquivo que falha se alguém escrever um número de corte ali.

`PASSO_PRATICA` aponta para `AdaptivePracticeService`, que já existia e já é o
que o microdiagnóstico usa por baixo. **Não há segundo motor de prática.** O
que faltava não era máquina: era alguém *dizer* ao aluno que o passo é praticar.

## 3. O que o frontend deixou de decidir

| antes (no JavaScript) | agora |
|---|---|
| `rotaDeProntidao()` sobre MOCK | `services/readiness_route.py` |
| `switch` montando o texto do resultado | `services/feedback_pedagogico.py` |
| escolha do próximo botão | `next_step.kind`, do backend |

O cliente traduz `next_step.kind` em palavras e `feedback.tom` em cor. Não
decide pedagogia.

### O feedback, por resultado

| decisão | o aluno lê |
|---|---|
| `PROCEED` | *"Muito bem! Você demonstrou um bom domínio de X. Essa base é importante para avançarmos em Y."* |
| `PREPARE` | *"Vamos revisar X antes de continuar. Alguns pontos ainda precisam ficar mais firmes. Esse conteúdo é uma base importante para compreender Y."* |
| `INSUFFICIENT` | *"Ainda não dá para concluir."* |

Dois testes guardam o tom: um varre `você domina`, `muito bem`, `parabéns` no
caminho de lacuna; outro varre `errou`, `fraco`, `deficiência`, `você não
sabe` — preparação é ajuda, nunca falta do aluno.

## 4. Fórmulas químicas

A inconsistência nasce no **conteúdo persistido**: 4 dos 14 itens foram
gerados com fórmula ASCII (`H2 + O2`), 10 com subscrito Unicode. O gerador não
foi instruído a padronizar.

`services/formula_quimica.py` distingue **índice** de **coeficiente**:

```
2 H₂O
^ ^
|  +-- índice (atomicidade) → subscrito
+----- coeficiente (quantas moléculas) → normal
```

Um replace ingênuo escreveria `₂ H₂O`, que diz outra coisa. 18 testes, com os
casos que quase me pegaram: `Fe3+` (carga, não índice), `Ca(OH)2`, `(NH4)2SO4`,
`item2` (não é molécula), e idempotência — rodar sobre texto já correto não
muda nada.

**Não é aplicado no Player compartilhado**, de propósito: ele serve
Matemática também, onde `2x2` não é fórmula. A normalização mora com o dono
dos itens, que é o Núcleo.

## 5. O campo "O que você precisa agora?"

`GET /api/v1/student/search` **existe e devolve questões reais**. O campo agora
tem botão **Enviar** (`type="submit"`, então Enter também envia) e mostra o que
encontrou.

O que **não** existe é abrir uma questão avulsa fora de uma atividade — e a
tela diz isso em vez de oferecer um link morto:

> *Abrir uma questão avulsa ainda não está disponível. Por enquanto elas
> chegam pelas atividades e pelas práticas.*

**Não há chat, e nada é simulado.** A lacuna é esta: mapear texto livre →
`content_code` para oferecer "praticar isto" seria interpretação que eu teria
de inventar. Ficou registrada, não implementada.

## 6. Meu Progresso

Os dados já eram reais; faltava hierarquia. Agora um cartão por conteúdo, com
ícone, nome e **o estado escrito** — a cor não carrega sozinha o significado.

"Revisar agora" aparece **apenas** quando `next_step.kind == PRACTICE` para
aquele conteúdo, ou seja, só quando há rota real. Nenhum botão morto.

## 7. Navegação

**Duas abas, não três.** Uma terceira ("Estudar" / "Minha trilha") levaria
exatamente ao que a Home já oferece — o próximo passo decidido pelo backend —
e uma aba que repete a anterior orienta menos, não mais. Quando houver trilha
de verdade, ela entra.

Os ícones viraram SVG: 🏠 e 📈 mudam de desenho e tamanho entre Android, iOS e
Windows, e a barra saía desalinhada.

## 8. Os dois caminhos, no navegador

**CAMINHO A — demonstra domínio**

```
DIAGNOSTIC → Balanceamento  (3 perguntas, 3 acertos)
  → "Muito bem! Você demonstrou um bom domínio de Reações químicas e
     balanceamento. Essa base é importante para avançarmos em Estequiometria
     e cálculos químicos."
  → Meu Progresso: Balanceamento [Consolidado]
  → próximo passo AVANÇA: Estequiometria
```

**CAMINHO B — não demonstra**

```
DIAGNOSTIC → Balanceamento  (3 perguntas, 0 acertos)
  → "Vamos revisar Reações químicas e balanceamento antes de continuar."
  → rota: PREREQUISITE_PREPARATION
  → botão: "Revisar agora"  →  PRÁTICA de 5 questões (não outro diagnóstico)
  → "Você acertou 1 de 5."
  → Meu Progresso: Balanceamento [Precisa de atenção] + "Revisar agora"
  → NÃO foi liberado para Estequiometria
```

Conferido em viewport de celular e de desktop, sem erro de console.

## 9. Dívidas que seguem

1. **A atividade de Estequiometria tem UMA questão.** Depois de dominar
   Balanceamento o passo vira "diagnosticar Estequiometria", e aí a tela diz
   *"Precisava de 3, tenho 1. Não vou adivinhar seu nível com menos que
   isso."* Honesto, mas é o fim da linha. Expandir o acervo estava fora do
   escopo deste bloco.
2. **Resolver a atividade oficial não existe.** O player funciona — é o mesmo
   que o diagnóstico e a prática usam — e falta ligá-lo.
3. **Texto livre → conteúdo.** A busca é real; a interpretação para oferecer
   prática a partir do termo digitado, não.
4. **A rota não é persistida em `study_sessions`.** A coluna existe (064), o
   piloto não abre sessão.
5. **A redistribuição de gabarito é posicional, não aleatória.** Determinística
   de propósito (auditável), mas quem vir muitos itens notaria A-B-C-D-E.
   Irrelevante para 3 por sessão.
