# Jornada pedagógica de Estequiometria — V1

Documento de engenharia e pedagogia do bloco **STOICHIOMETRY PEDAGOGICAL
JOURNEY V1** (2026-10-06 / 2026-10-07), sobre `fase6/vetorial`.

O bloco anterior deixou o sistema sabendo **onde** o aluno errou. Este faz com
que ele **investigue, ensine, guie, retire o apoio e verifique**.

---

## 1. O problema que o bloco encontrou

Medido em 2026-10-06, com aluno QA real, pela API real:

| entrada da decisão | valor | consequência |
|---|---|---|
| alvo | `MASSA_MOLAR` | o gargalo era conhecido |
| `ha_material` | `False` | não havia o que ensinar |
| `ha_guiada_pendente` | `False` | não havia o que guiar |
| decisão | `PRACTICE` | **cinco questões** |

As causas, auditadas:

- o único material de `CHEMISTRY-PHYSICAL-STOICHIOMETRY` no banco era
  **"lista teste"**, `PRIVATE` + `DRAFT` — um rascunho, não conteúdo;
- `item_para("CHEMISTRY-PHYSICAL-STOICHIOMETRY", ...)` devolvia `None`: só
  havia prática guiada escrita para Balanceamento.

Os degraus de cima da escada existiam no código e **não tinham o que servir**.
O aluno saía da sondagem com o gargalo identificado e recebia um lote de
exercícios — o encadeador adaptativo que o projeto inteiro existe para não ser.

---

## 2. O contrato da intervenção

`decidir_intervencao` passou de cinco saídas para seis. A nova não foi
acrescentada no fim da lista: ela entra **antes do ensino**.

```
1. VERIFICAR    vence tudo — quem se recupera não é interrompido
2. ESCALAR      antes de qualquer nova tentativa
3. INVESTIGAR   ← novo: a cadeia de microperguntas, se houver uma
4. ENSINAR      a explicação, agora sabendo o que explicar
5. GUIADA       tentar com ajuda, antes de tentar sozinho
6. PRATICAR     a única das seis que produz evidência de domínio
```

**Por que investigar vem antes de ensinar.** Despejar a resolução completa em
quem errou só a última etapa é repetir o que ele já sabia; em quem errou a
primeira, é construir três etapas sobre a que falhou. Nos dois casos o sistema
termina sem saber nada de novo — ele falou, não perguntou. A micropergunta
descobre qual dos dois casos é antes de a explicação ser gasta.

Tudo determinístico. `tests/test_percurso_sem_provedor.py` verifica pela AST
que nenhum dos 14 módulos do percurso importa provedor.

---

## 3. Micro-habilidades implementadas

Códigos do grafo (`grafo_estequiometria`), os mesmos que
`pedagogical_classifications.subcontent` já usava onde já existiam:

| código | conteúdo curado | investigação | item guiado |
|---|---|---|---|
| `LEITURA_DE_FORMULA` | ✓ | ✓ 3 etapas | ✓ Al₂(SO₄)₃ |
| `MASSA_MOLAR` | ✓ | ✓ 3 etapas | ✓ CO₂ |
| `RELACAO_MASSA_MOL` | ✓ | ✓ 3 etapas | ✓ CO₂ 88 g |
| `PROPORCAO_ESTEQUIOMETRICA` | ✓ | ✓ 3 etapas | ✓ N₂/H₂/NH₃ |

Os pré-requisitos **vêm do grafo**, não são redigitados: duas listas da mesma
coisa divergiriam, e a do material seria a errada, porque é a que ninguém
consulta.

---

## 4. Conteúdo curado, e por que é código

`services/conteudo_estequiometria.py`. Mesmo motivo de
`conteudo_balanceamento`: sendo dado estruturado no repositório, cada número
entra na suíte. A diferença é o verificador — lá é contagem de átomos
(`chemistry_balance`), aqui é aritmética de massas (`massa_molar`).

Por micro-habilidade: objetivo, pré-requisitos, erro esperado, explicação,
exemplo passo a passo, verificação.

O que **não** está no material, de propósito: perguntas guiadas
(`investigacao_do_erro`), prática com apoio (`itens_guiados`), critério de
retirada de apoio (`escada_de_apoio`). Duplicar qualquer um deles criaria duas
versões da mesma coisa divergindo na primeira correção.

### Fail closed

`conferir()` refaz **todas** as contas. Mutação verificada: trocar 17 por 16
derruba a suíte; escrever "NH3" sem subscrito derruba também.

```
MASSA_MOLAR
  Passo 1 — ler a fórmula            NH₃          = 1 N e 3 H
  Passo 2 — contribuição do N        1 × 14       = 14 g/mol
  Passo 3 — contribuição dos H       3 × 1        = 3 g/mol
  Passo 4 — somar                    14 + 3       = 17 g/mol
```

### O rascunho PRIVATE

Auditado e **deixado onde está**. Publicá-lo seria tornar público conteúdo que
ninguém auditou. O Assessor passou a ter material próprio
(`scripts/publicar_material_estequiometria.py`, `PUBLIC` + `PUBLISHED`,
idempotente, 4 seções / 12 blocos).

---

## 5. A investigação do erro

`services/investigacao_do_erro.py` (curado) +
`services/servico_de_investigacao.py` (persistência).

```
ERRO → hipótese → micropergunta → resposta → gargalo localizado
```

### A hipótese é hipótese

O distrator **sugere** um caminho; ele não o prova. 8,50 g é exatamente
`0,5 mol × 17 g/mol` — o que sai de levar o mol de N₂ direto para a massa de
NH₃. Isso é uma hipótese **boa justamente porque é testável**: a segunda
micropergunta a confirma ou a derruba, e até lá ninguém afirma nada.

Há teste varrendo os textos atrás de "você fez", "você esqueceu", "seu erro
foi" — e exigindo um marcador de suposição ("sugere", "pode ter", "vamos
conferir").

### Errar não avança

Quem errou a etapa 1 precisa da etapa 1. Perguntar a etapa 2 a quem acabou de
mostrar que a 1 não está de pé produz uma resposta que não significa nada.

### O gargalo é a PRIMEIRA etapa que falhou

Errar duas etapas não quer dizer duas lacunas: a segunda se apoia na primeira,
e começar pela mais alta é ensinar o telhado a quem não tem parede.

### Dois níveis de retorno

Medido no navegador (2026-10-06) e corrigido:

| tentativa | o que o aluno lê |
|---|---|
| 1ª | a **regra**, sem aplicá-la ao item, + "tente recontar — se ainda não sair, eu abro a conta" |
| 2ª+ | a regra **aplicada** |

A versão anterior dizia, na primeira falha, *"o índice conta os átomos daquele
elemento: em NH₃ são **três** hidrogênios"*. A regra está certa e a frase é
gentil — e ela entrega o número. O aluno clica em C sem recontar, a etapa fica
registrada como resolvida, e o sistema conclui que a micro-habilidade está de
pé quando a única coisa que aconteceu foi ele ler a resposta. **12 de 12
etapas faziam isso.**

---

## 6. A escada de apoio, e o fading

`services/escada_de_apoio.py`. Quatro degraus, do mais apoiado ao menos:

| | degrau | o que acontece | produz evidência? |
|---|---|---|---|
| L3 | `INVESTIGACAO` | uma micropergunta de cada vez | **não** |
| L2 | `ENSINO` | a explicação, focada no alvo | **não** |
| L1 | `GUIADA` | ele tenta, a dica chega se pedir | **não** |
| L0 | `AUTONOMO` | uma questão sozinho | **sim** |

Os três fatos que movem a escada já eram gravados por outros motivos
(`guided_practice_items`, `material_progress`). **Nenhuma tabela nova, nenhuma
coluna nova.**

`produz_evidencia(nivel)` é a invariante inteira, e é uma função do módulo —
não uma convenção de quem o usa.

### Fading dentro do degrau

A prática guiada tem quatro níveis de ajuda, e cada um reduz uma dificuldade
**diferente**: conceito → onde olhar → operação → assistida. Até o último o
aluno continua tendo de identificar a alternativa.

A investigação **não tem botão de dica**, de propósito: ela já é o degrau mais
alto, e um degrau acima do topo só levaria a entregar a resposta.

---

## 7. Evidência — as invariantes, medidas no produto

Aluno `aluno_qa_est_limpo`, pelo navegador, 2026-10-07:

| momento | `origin_breakdown` | respondidas |
|---|---|---|
| depois da sondagem | `{MICRO_DIAGNOSTIC: 3}` | 3 |
| depois de **1 investigação (3 etapas) + 1 guiada** | `{MICRO_DIAGNOSTIC: 3}` | 3 |
| depois da prática autônoma | `{PRACTICE: 5, MICRO_DIAGNOSTIC: 3}` | 8 |
| depois de 4 explicações + 1 conversa | `{PRACTICE: 8, MICRO_DIAGNOSTIC: 3}` | 11 |

As quatro interações assistidas contribuíram **zero**. As explicações e a
conversa contribuíram **zero**. Só o degrau autônomo moveu o mapa.

Onde a investigação é gravada: `guided_practice_items`, **uma linha por
etapa**. A tabela já existe, já tem a semântica certa (interação assistida) e
o mapa de domínio já não a lê, por projeto da PHASE 22 — a garantia é herdada,
não reconstruída.

Ganho de dado: cada linha grava a micro-habilidade que **aquela etapa** isola.
Antes o sistema sabia "errou uma questão de estequiometria"; agora sabe "a
conversão massa-mol está de pé, a proporção não".

---

## 8. Os dois modos da mesma tela

| | REVISÃO | RECUPERAÇÃO |
|---|---|---|
| quando | atividade entregue, nota dada | acabou de errar, sendo ajudado |
| mostra "você errou" | sim | sim (como "vamos olhar esta") |
| mostra **qual era a certa** | sim, imediatamente | só depois da explicação |

Revelar a alternativa certa no primeiro segundo da recuperação encerra a
recuperação antes de ela começar. O que se esconde é **qual era a certa**, e só
por um momento — o gabarito nunca fica escondido para sempre, e há teste de
que não existe caminho em que ele permaneça oculto.

---

## 9. O resultado deixou de ser o placar

A tela de resultado de uma atividade terminava em:

```
ATIVIDADE CONCLUÍDA
Você acertou 1 de 5.
[ Revisar questões ]
```

O placar como mensagem pedagógica, e um botão de volta ao que já passou —
enquanto o backend **já sabia** o próximo passo. Fim das questões virava fim
da jornada.

Agora, havendo intervenção a fazer: o título é a frase do backend, o botão
principal é o CTA dele, e o placar vira linha secundária. O placar **não
desaparece** — a escola recebe o número, e esconder do aluno o que a escola vê
seria outro problema. Sem próximo passo, o placar volta a ser o título: ali ele
é mesmo a notícia.

---

## 10. ESCALATE não quer dizer que o Edu desistiu

A versão anterior dizia *"o melhor próximo passo agora é conversar com seu
professor"* e, logo abaixo, oferecia *"Conversar com o Assessor"*. As duas
frases juntas diziam ao aluno que o sistema desistiu e que ele continuasse
nele.

ESCALATE quer dizer que **esta estratégia** chegou ao limite previsto. A frase
atual nomeia o que continua disponível — voltar à explicação, perguntar sobre
o ponto exato, levar a dúvida ao professor — e o cartão passou a poder reabrir
o material, porque "você pode voltar à explicação" sem porta é uma frase vazia.

**Nenhuma promessa de canal que não existe.** Não há vínculo endereçável
aluno-professor, nem endpoint, nem caixa de entrada. O texto recomenda procurar
o professor; não finge que a plataforma avisa alguém.

---

## 11. Provider off

O percurso essencial não depende de IA. Verificado pela AST em 14 módulos.

A IA entra em **um** lugar: a explicação de um erro. Lá há fallback honesto —
que não finge ser explicação e **não conta ao aluno a dívida técnica do
produto**. Resolução curada nunca é trocada por IA.

### O prompt que afirmava demais

Medido no navegador com provedor real (2026-10-07), o aluno leu:

> "Você provavelmente marcou 6 mol **por não perceber que** os coeficientes da
> equação representam a proporção entre as substâncias."

> "Você provavelmente encontrou 2 mol de NH₃, mas **dobrou a proporção** de H₂
> e chegou a 6 mol."

As duas afirmam uma **operação mental**. O sistema observou uma letra marcada —
nada mais. "Provavelmente" no começo não desfaz o resto: o primeiro hesita, o
resto afirma. Um aluno que chutou recebe ali um diagnóstico falso sobre si, e
aprende que o sistema inventa.

A causa estava no **pedido**: a regra 1 de `explicacao_v1` dizia "comece pelo
que ele PROVAVELMENTE FEZ". O modelo obedeceu.

`explicacao_v2` proíbe a afirmação e **oferece a forma alternativa** ("esse
resultado costuma aparecer quando…"), porque proibir sem dar a forma produz
texto evasivo. A v1 fica no registro: ela é o que explicou para quem leu
aquelas telas.

Conferido em três amostras depois da troca: nenhuma das oito formas de
afirmação varridas apareceu.

---

## 12. Casos de aceitação

### Massa molar (§6)

`NH₃`, N = 14, H = 1. A cadeia pergunta, em ordem: quantos H na fórmula → qual
a contribuição dos três H → qual o total. A primeira etapa é de
`LEITURA_DE_FORMULA`, não de `MASSA_MOLAR`: perguntar a contribuição dos
hidrogênios a quem lê 1 H no NH₃ não localiza nada.

### N₂ → NH₃, 8,50 g (§8, §30)

`N₂ + 3 H₂ → 2 NH₃`, 14,0 g de N₂, M(N₂) = 28,0, M(NH₃) = 17,0. Correta:
17,0 g. O aluno marcou 8,50 g.

| etapa | habilidade | conta | esperado |
|---|---|---|---|
| 1 | `RELACAO_MASSA_MOL` | 14,0 ÷ 28,0 | 0,5 mol de N₂ |
| 2 | `PROPORCAO_ESTEQUIOMETRICA` | 0,5 × 2 ÷ 1 | 1,0 mol de NH₃ |
| 3 | `RELACAO_MASSA_MOL` | 1,0 × 17,0 | 17,0 g |

8,50 g é `mol_para_massa(massa_para_mol(14,0, "N₂"), "NH₃")` — e há teste
dessa igualdade, para que o texto que a cita não envelheça. A etapa 2 é a que
separa 17,0 de 8,50, e é ela que a hipótese aponta **como suposição**.

---

## 13. Limitações conhecidas

1. **A sondagem não garante servir os itens curados.** Medido: dos 3 itens
   servidos, um era questão do banco com 5 alternativas, não um dos 5 curados.
   A propriedade "um item discriminativo por micro-habilidade" depende de o
   seletor escolher o item curado. O percurso funciona (o gargalo foi
   localizado e a intervenção veio), mas a discriminação não é garantida.
   **Próximo bloqueio candidato.**

2. **A linha da investigação não guarda qual distrator foi marcado.** A tabela
   não tem coluna, e o bloco preferiu zero migration. Hoje não faz diferença —
   a hipótese é curada por micro-habilidade, não montada em tempo de execução.
   Se a hipótese passar a depender do distrator, aí haverá necessidade
   arquitetural de uma coluna, e não antes.

3. **A cadeia é cumulativa por projeto.** A resposta de uma etapa é derivável
   da etapa anterior mais o enunciado — é isso que a torna uma cadeia e não
   três testes independentes. Não é vazamento; é a estrutura.

4. **Nenhum teste garante a saída do modelo.** O prompt é travado; a obediência
   é verificada no navegador, por amostragem.

5. **Quatro micro-habilidades de nove.** `CONCEITO_DE_MOL`,
   `LEITURA_DE_COEFICIENTE`, `RELACAO_MOL_MOL`, `RELACAO_MASSA_MASSA` e
   `ESTEQUIOMETRIA_INTEGRADA` não têm conteúdo curado, investigação nem item
   guiado. Alvo nessas habilidades cai no degrau de baixo disponível.

6. **Um aluno QA foi descartado.** `aluno_qa_jornada_est` acumulou estado que
   não corresponde às ações registradas desta sessão (6 tentativas numa etapa
   onde houve 1 clique; o log mostra uma caminhada por script nas portas
   59412–59543). A causa não foi determinada. O QA de navegador foi refeito do
   zero em `aluno_qa_est_limpo`.

---

## 14. Alunos QA

| identidade | código | propósito (`qa_purpose`) | estado |
|---|---|---|---|
| `aluno_qa_jornada_est` | PILOTO-0008 | `V1_JORNADA_ESTEQUIOMETRIA` | descartado (ver 13.6) |
| `aluno_qa_est_limpo` | PILOTO-0009 | `V1_JORNADA_ESTEQUIOMETRIA` | o do QA relatado |

Todos marcados em `user_school_links.metadata ->> 'qa_purpose'`, na Escola ABC
/ Turma 3ª A (Piloto). Limpeza: selecionar por `qa_purpose` e remover
`user_school_links`, `student_enrollments`, `students`, `users`, `persons`,
mais `guided_practice_items`, `activity_attempts`, `activity_results` e
`domain_content_mastery` por `student_external_id`.

### `aluno_teste_jornada`

Reservado para validação manual do usuário; **não usado no desenvolvimento**.

Auditado em 2026-10-07: tem 5 `activity_attempts` e 5 `activity_results`,
todos criados em **2026-10-06 21:32–21:41** — fora desta sessão, e o uso para
o qual ele existe.

**Uma escrita minha:** ao verificar o estado dele eu chamei
`POST /student/domain/rebuild` com essa identidade, o que materializou uma
linha em `domain_content_mastery` (23 respondidas / 5 corretas, accuracy
0,2174). A linha é **derivada** da evidência que já existia; nenhuma resposta
foi acrescentada ou alterada. Não a apaguei: seria uma segunda escrita não
autorizada sobre o mesmo dado.

---

## 15. Migration

**Zero.** Alembic head continua `067_guided_practice`. A investigação reusa
`guided_practice_items`; a escada deriva de dados já gravados; o conteúdo
curado usa as tabelas da PHASE 23.
