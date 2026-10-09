# Núcleo Diagnostic Bank — Estequiometria

**2026-10-04.** O gargalo do Piloto Zero era uma questão de Estequiometria no
acervo. Agora são 21 selecionáveis, e o ciclo fecha.

---

## 1. Taxonomia: nada novo foi criado

| conceito | já existe? | código | lacuna |
|---|---|---|---|
| Estequiometria | **sim** | `CHEMISTRY-PHYSICAL-STOICHIOMETRY` | — |
| Balanceamento (pré-requisito) | **sim** | `CHEMISTRY-GENERAL-BALANCING` | — |
| arco `REQUIRES` | **sim** | `catalog_node_prerequisites` | — |
| proporção, mol-mol, massa-mol, massa-massa | **não** | — | micro-habilidade |

A arquitetura **já representa micro-habilidade** sem `CatalogNode`:
`diagnostic_skill` no metadata do item, espelhado em
`PedagogicalClassification.subcontent` e `skills`. É o que o banco de
Balanceamento faz desde o primeiro dia.

**Nenhum nó novo**, de propósito: nada é pré-requisito de "relação mol-mol".
Essas habilidades não participam do grafo curricular — virariam nós sem
aresta, carregando um vocabulário que o planejador teria de aprender a
ignorar.

## 2. A matriz, e o que ficou de fora

| habilidade | o que descobre |
|---|---|
| `PROPORCAO_ESTEQUIOMETRICA` | lê a proporção a partir dos coeficientes? |
| `RELACAO_MOL_MOL` | converte mol de uma espécie em mol de outra? |
| `RELACAO_MASSA_MOL` | usa a massa molar para ir de massa a mol? |
| `RELACAO_MASSA_MASSA` | percorre massa → mol → proporção → mol → massa? |

**Reagente limitante, rendimento e pureza ficaram fora.** As três exigem um
segundo dado de entrada (duas massas, ou um percentual) e portanto um contrato
de item que a verificação determinística ainda não sabe refazer. Entrar com
elas agora significaria **aprovar por opinião de modelo o que não consigo
recalcular** — exatamente o que este banco existe para não fazer. Ficam
registradas como a próxima matriz.

## 3. Verificação determinística (§5)

```
          equação declarada
                 ↓
      proporcao_molar  ──► RECUSA se não estiver balanceada
                 ↓
   massa molar (IUPAC) + coeficientes
                 ↓
         valor numérico da resposta
                 ↓
     compara com as 5 alternativas
```

O que é conferido, sem LLM nenhum:

- a equação **está realmente balanceada** (reusa `chemistry_balance`);
- massa molar de cada espécie, pela tabela da IUPAC;
- proporção molar coerente com os coeficientes;
- **o valor da resposta**, recalculado do zero;
- exatamente uma alternativa bate;
- as cinco são numericamente distintas;
- cada alternativa tem número legível;
- dados suficientes, sem massa negativa nem divisão por zero.

### O erro que isto existe para pegar

**Equação não balanceada.** A regra de três fecha perfeitamente sobre
coeficientes errados, o número que sai parece certo — e um **segundo LLM
refaria a mesma conta sobre a mesma equação errada e concordaria**. Os dois
estariam errados juntos. Por isso `proporcao_molar` recusa antes de calcular.

### Tolerância, e por que é relativa

O livro escolar usa H=1 e O=16; a IUPAC diz 1,008 e 15,999. Para 4 g de H₂ o
livro escreve 36 g e a aritmética dá 35,74 — **0,7%**. Comparar por igualdade
rejeitaria itens escolares corretos. `TOLERANCIA_RELATIVA = 1%`, declarada num
lugar só.

## 4. O banco

| skill | gerados | AI_VERIFIED | REQUIRES_REVIEW | EASY | MEDIUM | HARD |
|---|---:|---:|---:|---:|---:|---:|
| `PROPORCAO_ESTEQUIOMETRICA` | 7 | **6** | 1 | 2 | 4 | 0 |
| `RELACAO_MASSA_MASSA` | 7 | **5** | 2 | 1 | 4 | 0 |
| `RELACAO_MASSA_MOL` | 7 | **5** | 2 | 2 | 3 | 0 |
| `RELACAO_MOL_MOL` | 4 | **4** | 0 | 1 | 2 | 1 |
| **total** | **25** | **20** | **5** | 6 | 13 | 1 |

**Distribuição do gabarito: A 4 · B 4 · C 4 · D 4 · E 4.**

### Dois aprovados

> *Na reação N₂ + 3 H₂ → 2 NH₃, qual massa de NH₃ é produzida a partir de
> 6,0 g de H₂?*
> Python calculou **33,79** → gabarito A = "34,0 g" ✓ (0,6%, dentro da tolerância)

> *Na reação 1 N₂(g) + 3 H₂(g) → 2 NH₃(g), 6,0 mol de H₂ reagem completamente…*
> Python calculou **68,12** → gabarito B = "68,0 g" ✓

### Dois rejeitados, e por quê

> *Na reação N₂ + H₂ → NH₃, qual massa de NH₃ a partir de 28 g de N₂?*
> **gabarito declarado 'E', mas o cálculo dá 'B' (34,04)** — o gerador marcou
> a alternativa errada na própria questão que escreveu.

> *…são consumidos 36 g de H₂O para formar os produtos…*
> **química incorreta**: o enunciado diz que a água é consumida, mas na
> equação ela é formada.

### O caso em que só a aritmética pegou: **não houve**

Nos dois itens rejeitados por cálculo, o verificador-LLM **também** resolveu
sozinho e discordou do gerador. Nesta amostra os dois concordaram.

Isso não torna a verificação determinística dispensável — ela é o que impede
que a aprovação dependa de o modelo ter tido um bom dia. Mas reportar que ela
"salvou o banco" seria inventar: nesta rodada, não salvou.

### O caso inverso aconteceu, e era meu

**4 dos 16 primeiros itens foram rejeitados por defeito do meu parser**, que
não reconhecia estado físico e respondia *"O2 não está na equação: ['KCl(s)',
'KClO3(s)', 'O2(g)']"*. Era química correta recusada por notação.

O fail-closed estava **certo** em recusar o que não entendia; estreito demais
era o parser. Corrigido com teste, os 25 itens foram **reavaliados sem gastar
IA de novo** — o veredito do verificador já estava salvo, e o que mudou foi a
aritmética.

## 5. Viés posicional — medido ANTES da carga

A lição do bloco anterior foi que a correção ficou no pipeline de geração e o
artefato que virou banco era anterior a ela. Desta vez o teste existe **antes**
e falha se o artefato não estiver lá:

```
AssertionError: artefato de Estequiometria ausente … Sem ele não há como
afirmar que o banco não tem viés — e afirmar sem medir foi o erro do bloco
anterior.
```

E o teste não compara percentuais: roda a **política real** e exige que
marcar sempre a mesma letra (A, B, C, D ou E) **não libere o aluno**.

## 6. Seleção pelo caminho real (§10)

Não pela tabela — por `QuestionBankService` → `PracticeSelectionPolicy` →
`MicroDiagnosticService`:

```
sufficient: True | disponíveis: 21 | pedidas: 3 | selecionadas: 3
excluídas por visual: 0 | protegidas: 0 | origin: MICRO_DIAGNOSTIC
```

**21 ≫ `min_sample_size` = 3.**

## 7. O ciclo, provado no navegador

```
ETAPA 1  Atividade de Estequiometria                      [tela]
ETAPA 2  → DIAGNOSTIC: Balanceamento (a base primeiro)    [tela]
ETAPA 3B → 3/3 → "Muito bem!" → alvo AVANÇA
ETAPA 4  → DIAGNOSTIC: Estequiometria, 21 disponíveis     [tela]
ETAPA 5  → responde itens reais com fórmulas em subscrito [tela]
ETAPA 6  → evidência MICRO_DIAGNOSTIC
ETAPA 7  → domínio: 3 respondidas, accuracy 1.0
ETAPA 8  → readiness: DIRECT → ACTIVITY                   [tela]
```

Meu Progresso ao final: **Balanceamento [Consolidado] · Estequiometria
[Consolidado]**. Celular e desktop, sem erro de console.

### E o contrário

Errando 3 de 3 em Estequiometria: `PREPARE_PREREQUISITE`, rota
`PREREQUISITE_PREPARATION`, passo `PRACTICE`. **Não liberado.**

## 8. O falso-pronto que isto encontrou

Na primeira passagem do Caminho B, o aluno errou **3 de 3** em Estequiometria
e a rota virou **DIRECT → ACTIVITY**. Ele foi liberado para a atividade
errando tudo.

A causa: `RECOMMENDED` estava na lista de estados que liberam. O planejador
marca o conteúdo como RECOMMENDED assim que há **qualquer** evidência —
RECOMMENDED quer dizer *"pratique isto"*, não *"está pronto"*.

**O estado do planejador diz se HÁ evidência. Quem diz se ela é BOA é a
política.** Agora `proximo_passo` consulta a banda antes de liberar, igual já
fazia para o pré-requisito. Quatro testes de regressão.

## 9. Micro-habilidades (§12)

`diagnostico_por_habilidade` agrega as respostas por `diagnostic_skill` e só
produz texto quando há **contraste** — uma habilidade forte e uma fraca, as
duas com amostra suficiente.

No ciclo real isso **não apareceu**, e está certo: as 3 perguntas caíram todas
em `PROPORCAO_ESTEQUIOMETRICA`, então `suficiente=True` e `texto=None`. Sem
contraste não há o que dizer de específico, e uma resposta por habilidade não
distingue quem sabe de quem chutou — a chance no chute é 1 em 5.

`INSUFFICIENT_EVIDENCE` continua sendo uma resposta válida.

## 10. Dívidas

1. **A atividade oficial tem 1 questão** e resolvê-la ainda não existe. O
   aluno chega em "Pronto para começar" e a tela de entrada diz que a
   resolução não está disponível. Era o escopo do bloco, e segue de pé.
2. **Reagente limitante, rendimento e pureza** — próxima matriz, com o
   contrato de item que a verificação precisa.
3. **Texto por habilidade raramente aparece** com 3 perguntas e 4
   habilidades. Para vê-lo seria preciso um diagnóstico mais longo, ou
   acumular evidência entre sessões.
4. **A redistribuição de gabarito é posicional**, determinística de propósito.
5. **`massas_molares` do item não é usado** pela verificação — ela calcula
   pela tabela da IUPAC em vez de confiar no que o gerador declarou. É o
   comportamento certo, mas o campo ficou no dataclass sem uso.
