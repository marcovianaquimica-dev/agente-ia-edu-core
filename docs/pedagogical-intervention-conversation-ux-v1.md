# Intervenção pedagógica na prática formativa — V1

Documento do bloco **PEDAGOGICAL_INTERVENTION_CONVERSATION_UX_V1**
(2026-10-08), sobre `fase6/vetorial`.

> Quando o estudante erra durante a prática formativa, o Edu precisa perceber
> que algo aconteceu.

---

## 1. A causa raiz — rastreada, não suposta

O §5 pede o caminho completo. Ele é este:

```
frontend   avancar()  →  d.pos += 1   (incondicional)
API        PUT .../attempt/answers/{vid}
serviço    save_answer  grava `selected_option_key` e devolve um RECIBO
                        — saved, position, answered_count.
                        NUNCA consultava o gabarito.
correção   attempt/correct, só depois do lote inteiro
```

As oito perguntas, respondidas:

| # | pergunta | resposta |
|---|---|---|
| 1 | o backend recebeu a resposta incorreta? | **sim** |
| 2 | foi classificada corretamente? | **não** — nada era classificado naquele momento |
| 3 | o motor produziu decisão? | **não** — não havia observação de onde partir |
| 4 | o frontend ignorou a decisão? | **não** — não existia decisão para ignorar |
| 5 | o backend devolveu o próximo item? | devolveu o **recibo**; o cliente avançou sozinho |
| 6 | a prática era um lote rígido? | **sim** |
| 7 | havia diferença indevida entre diálogo e prática? | **sim** — ver abaixo |
| 8 | a intervenção foi postergada por regra ou por falta de integração? | **falta de integração** |

A assimetria da pergunta 7 é o achado que explica tudo:
`servico_de_investigacao.responder` **confere na hora** e devolve `correct`,
`observacao`, o estado da hipótese e o próximo passo. A prática, não. Os dois
caminhos do mesmo produto tratavam uma resposta de formas diferentes.

---

## 2. O portão: quatro modos, um intervém

O §4 pede a distinção antes da correção. Ela **não exigiu campo novo**: o
`metadata` da atribuição já carregava `origin` e `purpose` desde que a
prática existe. Medido no banco:

| metadata | modo | erra → |
|---|---|---|
| `origin=MICRO_DIAGNOSTIC` | DIAGNÓSTICO | **segue** — ensinar no meio contamina a observação seguinte |
| `origin=PRACTICE, purpose=PRACTICE` | **FORMATIVA** | **intervém** |
| `origin=PRACTICE, purpose=VERIFY` | VERIFICAÇÃO L0 | **segue** — dica durante a tentativa destrói a independência |
| sem a marca `practice` | AVALIAÇÃO FORMAL | **segue** — ensinar na prova viola a política |

Há teste exigindo que **exatamente um** dos quatro devolva `True`, para que
acrescentar um modo obrigue a decidir.

**Fail-closed, e a direção importa.** Metadata ausente ou desconhecido vira
avaliação formal. Errar para o lado restritivo deixa um aluno sem uma
intervenção; errar para o outro interrompe uma prova.

---

## 3. O contrato de transição

```
RESPOSTA → OBSERVAÇÃO → DECISÃO PEDAGÓGICA → INTERVENÇÃO → NOVA TENTATIVA
```

`intervencao_formativa.decidir_apos_resposta` não decide pedagogia nova:

1. **investigação curada pendente** para aquela micro-habilidade →
   `INVESTIGATE`, pela mesma `InvestigacaoService` do diálogo;
2. senão, **material publicado** → `TEACH`, pelo mesmo
   `StudentMaterialService` da preparação;
3. nenhum dos dois → **vazio**, e o aluno segue.

A ordem é a da escada de apoio: investigar antes de ensinar, porque a
micropergunta é mais barata e descobre *o que* explicar. O terceiro caso é
honesto: prender alguém numa questão sem ter com que ajudá-lo transformaria a
correção em bloqueio.

**O alvo é a micro-habilidade do ITEM**, não a do conteúdo: "errou
estequiometria" não diz onde intervir.

**Nada disso escreve evidência.** É leitura e decisão; a correção da
tentativa e o Evidence Engine continuam exatamente onde estavam, e há teste
varrendo `activity_results`, `activity_result_items` e
`domain_content_mastery` depois de uma decisão.

---

## 4. Dois defeitos que só a API mostrou

O teste de serviço passou verde enquanto a plataforma, no navegador,
continuava servindo a próxima questão. Os dois defeitos moravam exatamente
entre o serviço e o cliente:

1. **MissingGreenlet engolido.** `save_answer` lia `row.metadata_` **depois**
   do commit; com `expire_on_commit=True` — como a aplicação — isso tenta
   recarregar fora do contexto async. O `except` da decisão engolia, e a
   resposta errada voltava com `may_advance: true`. O teste usava
   `expire_on_commit=False` e por isso não via. A fixture foi corrigida, e
   ela imediatamente encontrou um segundo MissingGreenlet — no meu próprio
   helper de teste.

2. **O `response_model` descartando o campo.** O serviço calculava
   `pending_intervention` corretamente e `ActivityPlayerState` não o
   declarava, então o Pydantic o removia em silêncio. O F5 devolvia a
   próxima questão.

`tests/test_intervencao_formativa_http.py` existe por causa dos dois, e
mutar o schema o faz falhar — verificado.

---

## 5. A tela

`avancar()` fazia `d.pos += 1` incondicional, e `escolher()` **descartava** a
resposta do autosave. Agora a decisão vem do backend e o passo é perguntado a
`IntervencaoUI.proximoPasso`.

**O botão executa, não avisa.** "Vamos entender o que aconteceu" sozinho não
resolve nada. O cartão abre a investigação daquela micro-habilidade ou o
material daquele conteúdo, e ao terminar o aluno volta **para a mesma
questão** — não para o próximo passo da jornada, que o mandaria praticar de
novo o que ele deixou no meio.

### Uma intervenção por questão

O backend continua dizendo que há algo a trabalhar — e está certo. Mas
oferecer a mesma explicação a quem acabou de lê-la é o que o próprio motor
evita no ciclo. O que a tela não repete é o **convite**, não a decisão.
Custo declarado: ao recarregar, a lista se perde e a intervenção daquela
questão pode ser oferecida mais uma vez.

---

## 6. §11 — dois canais, um principal

A tela mostrava A/B/C/D e um campo "Sua resposta" lado a lado, sem
explicação. Os dois são legítimos: quem digita "3" e quem toca em "3" dizem a
mesma coisa, e o backend as lê igual. Então a correção não foi tirar um — foi
dizer qual é o principal.

**Escrever vem primeiro**, porque isto é uma conversa. Os atalhos vêm depois,
sob **"Ou toque numa opção:"**. Há teste exigindo que nunca apareçam
alternativa e campo concorrendo sem rótulo, e que o rótulo não sugira que
escolher seja obrigatório.

---

## 7. §12 — espaçamento, medido

Não havia token de espaçamento nenhum: cada regra escolhia um valor. Agora há
`--e-p` (12px), `--e-m` (16px) e `--e-g` (24px).

| | antes | agora |
|---|---|---|
| campo → "Responder" | 8px | **12px** |
| atalhos → "Não sei" | — | **24px** |
| "Não sei" → "Voltar ao início" | adjacentes | **48–56px** |
| alvo de toque mínimo | campo a 43px | **44px** |

E **"Voltar ao início" saiu do cartão da resposta**: ele é navegação, e
grudado abaixo de "Não sei" parecia a terceira alternativa da pergunta.

Medido em 320, 390, 768 e 1280: zero scroll horizontal, zero elementos
estourando, nenhum alvo abaixo de 44px, campo a 16px.

---

## 8. Browser QA — o que foi medido

| jornada | resultado |
|---|---|
| **1. erro formativo** | errou Na₂O, clicou "Próxima" → *"Essa parte ainda está travando. Vale olhar a explicação antes de seguir."* + botão que abre o material |
| **2. investigação** | `15` → hipótese hedgeada → investigação continua |
| **3. pré-requisito** | `15` → `1` → `bottleneck_skill = LEITURA_DE_FORMULA`, regra ensinada **sem o número** |
| **4. mudança de estratégia** | 1ª: conceito. 2ª (após "Explique de outro jeito"): exemplo resolvido em 3 passos. **Mudança real** |
| **5. verificação L0** | a tela da prática não tem botão de dica; evidência registrada pelo caminho oficial |
| **6. interface** | 320/390/768/1280 limpos; F5 no meio da prática **retoma a intervenção** |
| **diagnóstico** | errou a 2ª das 3 e o diagnóstico **seguiu** — §4-A preservado |

---

## 9. Evidence Engine — antes e depois

`aluno_qa_interv1`, depois da jornada inteira:

```
4 corretas de 6 respondidas
evidence_state = OBSERVED
origin_breakdown = {'PRACTICE': 3, 'MICRO_DIAGNOSTIC': 3}
```

`OBSERVED`, não uma banda. Política intocada — nem corte, nem
`min_sample_size`. A conversa ganhou turnos e a evidência **não inflou**: a
intervenção não escreve em `activity_*` nem em `domain_content_mastery`, e há
teste varrendo as três tabelas.

---

## 10. Limitações declaradas

1. **A de-duplicação do convite é de sessão.** Ao recarregar, a intervenção
   daquela questão pode ser oferecida mais uma vez (§5).

2. **A governança do avanço é por contrato, não por bloqueio.** O backend
   decide e diz `may_advance: false`; um cliente que ignorasse o campo ainda
   conseguiria responder o item seguinte. Bloqueá-lo exigiria recusar
   `save_answer` fora da ordem, o que quebraria a retomada.

3. **Duas ações.** `INVESTIGATE` e `TEACH`. `REVISAR_PREREQUISITO` existe,
   mas como consequência da investigação (o `bottleneck_skill` desce), não
   como ação própria. `REPETIR_COM_VARIACAO` e `OFERECER_APOIO` não foram
   implementadas.

4. **Conteúdo sem material e sem investigação não recebe intervenção.** O
   aluno segue, e a decisão registra o motivo. É o caso da maioria do
   catálogo.

5. **A posição corrente é a única questão com intervenção pendente.** Se o
   aluno errar a 1 e a 3, só a da posição corrente volta depois do F5.

6. **Provider-on continua não exercitado** no diálogo. A explicação do erro
   usa IA e tem fallback — foi ela que produziu a mudança de estratégia da
   jornada 4.
