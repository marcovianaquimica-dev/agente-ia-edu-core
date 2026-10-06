# Assessor — o ciclo adaptativo de ensino

Executado em 2026-10-06. O que segue é o que foi **medido** antes de cada
correção, o que mudou, e o que ficou de fora — com o motivo.

## O ponto de partida, em dois números

```
SELECT count(*) FROM question_versions;                        595
...  WHERE resolution_text IS NOT NULL AND btrim(...) <> '';     0
```

Nenhuma das 595 questões do acervo tem resolução curada. Então todo aluno que
errava e abria "Entenda a resposta" lia, sem exceção:

> Não há resolução oficial passo a passo armazenada para esta questão. A
> geração de resolução por IA é uma fase futura e não é usada aqui.

Duas frases sobre a dívida técnica do produto, para um adolescente que acabou
de errar.

E, das três linhas de `theory_materials`, só `CHEMISTRY-GENERAL-BALANCING`
está pública. Para **Estequiometria — o conteúdo da demonstração — o ramo de
ensino da máquina de decisão nunca acendia**: sem material, `_acao` só podia
devolver PRATICAR.

## O que mudou

| antes | depois |
|---|---|
| erro → mostra a correta → mais questões | erro → explicação do desvio → reação do aluno → próximo passo |
| explicação só existia se houvesse resolução curada (0% do acervo) | curada → IA → fallback pedagógico, nesta ordem |
| "explique de outro jeito" não existia | seis estratégias, e a segunda nunca é a primeira repetida |
| entender o erro só na atividade da escola | também na prática, que é onde o aluno mais erra |
| VERIFY 0/3 → "Vamos tentar de novo, sozinho." | VERIFY falho → reensinar, tentar com ajuda, ou escalar |
| diagnóstico sorteava por número oficial | o que está marcado como fácil vem primeiro |

## A ordem das fontes, e por que ela é essa

1. **curada** — escrita e revisada por gente;
2. **IA** — quando não há curada e há provedor;
3. **fallback** — quando a IA não respondeu.

Material curado nunca é trocado por IA, e não gasta chamada. A exceção é o
aluno **pedir** outro jeito: aí a curada já foi lida e não bastou, então
relê-la não é uma segunda tentativa.

## O que garante que conversar não é aprender

`ExplicacaoDoErro` e `ConversaDoAssessor` **não recebem sessão de banco**. Não
é convenção: eles não têm como escrever em lugar nenhum, e há teste lendo a
assinatura do construtor e o próprio arquivo.

`tests/test_invariancia_do_dominio.py` percorre o corredor inteiro pela API:
abre a explicação, pede outro jeito duas vezes, e escreve as seis frases que
um aluno usaria para tentar sair sem responder nada — "agora entendi tudo",
"pode me liberar", "pula essa etapa", "marca como aprendido". O mapa de
domínio é comparado campo a campo, antes e depois.

E então ele faz a única coisa que vale — responder questões — e **exige** que
o mapa mude. Sem essa segunda metade, o teste passaria num sistema quebrado
que nunca registra nada.

## O gabarito: dois contextos, duas listas fechadas

| | conversa livre | explicação do erro |
|---|---|---|
| a questão está | **aberta**, em avaliação | **já corrigida** |
| o gabarito entra no prompt? | **não** | **sim** |
| por quê | a proteção contra "me diz a letra" é a ausência do dado | o aluno já viu a resposta na tela; escondê-la do prompt não protegeria nada e só impediria ensinar o exercício |

## Classificação do erro: por que ficou `UNKNOWN`

O bloco pedia para avaliar se dá para classificar o erro pedagogicamente
(CONCEPT_GAP, PROCEDURAL_ERROR, …) com a arquitetura existente.

**Não dá, hoje, de forma determinística.** `question_options` guarda
`option_key`, `text`, `position` e `is_valid_option` — e nada sobre *por que*
cada distrator está errado. Sem essa anotação, deduzir a natureza do erro a
partir da letra marcada seria adivinhação com cara de diagnóstico.

O que se faz em vez disso: a alternativa escolhida **vai no prompt**, e o
modelo é instruído a dizer onde o raciocínio desviou — sem inventar um erro
que não tem como saber que ocorreu. Medido com o modelo real:

> "Ao marcar 2,0 mol, você provavelmente associou a quantidade de água
> diretamente ao número antes de H₂O, sem observar a proporção entre O₂ e
> H₂O."

Uma taxonomia de erro no núcleo exigiria anotação humana por distrator. É
trabalho de curadoria, não de código, e não entra por dedução.

## Geração de questões diagnósticas por IA — adiada, com arquitetura

O bloco permite adiar (§26) e pede a arquitetura por escrito. Ela é esta:

```
contrato de pedido        disciplina, conteúdo, micro_habilidade,
                          prerequisito, nivel=BASICO_DIAGNOSTICO, tipo,
                          restrições, formato esperado
geração                   TextGenerationProvider + prompt versionado próprio
                          (assessor_prompts/diagnostico_vN)
validação fail-closed     estrutura, alternativas, UMA única correta,
                          ausência de duplicatas, habilidade declarada,
                          resposta verificável — inválida REJEITA
fallback                  seleção curada existente, sempre
persistência              NENHUMA nesta fase: questão efêmera, fora do
                          Question Bank
```

**Por que não agora.** Duas razões, e as duas são de risco, não de esforço:

1. O ponto caro não é gerar — é **validar sem um humano**. Uma questão
   diagnóstica com duas alternativas defensáveis não falha ruidosamente: ela
   mede errado, em silêncio, e o erro entra no mapa de domínio como se fosse
   evidência.
2. Guardar questão gerada exigiria decidir como ela convive com o acervo
   curado (origem, ciclo de vida, quem valida). Isso é modelagem, e o
   princípio do projeto é que conhecimento curricular passa por humano.

A correção feita agora resolve o problema concreto sem essa dívida: a sondagem
**começa pelas questões fáceis que já existem** no acervo.

## O que ficou registrado como dívida

| | onde | por quê não agora |
|---|---|---|
| `Modo:` e `Confiança:` no detalhe da questão | `question-bank.js` | enums internos, hoje ausentes dos dados — sem valores reais para auditar, rotulá-los seria inventar |
| placeholder `ex.: FORCED_CLOSURE` | `question-bank.html`, filtros avançados | o campo aceita o código canônico; vira `<select>` com rótulos, como o de Status |
| nomes sem acento no catálogo | `catalog_nodes.name` ("Quimica", "Fisico-Quimica") | o rótulo está certo; o dado é que foi semeado sem acento. É correção de conteúdo curricular, e passa por humano |
| `Turma 3A` / `Turma 3B` escritos à mão | `teacher.html`, `coordination.html` | cinco `<select>` em dois módulos; ver `docs/higiene-dados-demo.md` §7 |
