# Testes do protótipo do Aluno, antes do Piloto Zero

**2026-10-04.** Estes dois arquivos **não rodam mais** e estão aqui por
memória, não por dívida de conserto.

## O que eles mediam

`aluno.js` como protótipo: oito estados de Home desenhados a partir de um
objeto `MOCK`, um planejador de sessão que repartia minutos em blocos
(`STUDY` / `PRACTICE` / `OBJECTIVE` / `REVIEW`), e a decisão de prontidão
calculada no próprio navegador.

28 testes, quase todos **expressões regulares sobre o código-fonte**:

```js
assert.match(js, /objetivoFicouDeFora[\s\S]{0,120}a atividade fica para a próxima/)
```

## Por que saíram

O Piloto Zero substituiu esse protótipo. O conteúdo pedagógico passou a vir
do servidor, e três coisas que eles mediam **deixaram de existir de
propósito**:

| o que media | o que aconteceu |
|---|---|
| o objeto `MOCK` e os oito estados | removidos — §12 do pedido: mock pedagógico não pode parecer real |
| o planejador de blocos de tempo | removido — repartia minutos sobre dados inventados |
| `rotaDeProntidao` no cliente | **moveu para o backend**, `services/readiness_route.py` |

Não é que eles quebraram. Eles continuam medindo corretamente um programa
que não é mais este.

## O que ocupou o lugar

| antes | agora |
|---|---|
| regex sobre `aluno.js` | `tests/test_piloto_zero_e2e.py` — 10 testes pelo HTTP real |
| `rotaDeProntidao` no JS | `tests/test_readiness_route.py` — 10 testes da função e do contrato com a migration 064 |

A diferença que importa: os novos **executam** o ciclo (resposta → correção →
evidência → domínio → nova decisão) em vez de procurar palavras no arquivo.

## Dívida honesta

A cobertura de *renderização* do protótipo não foi reposta inteira. O que
os testes antigos checavam sobre microcópia e presença de controles hoje só
é verificado à mão, no navegador. Está registrado em
`docs/piloto-zero-aluno.md`.
