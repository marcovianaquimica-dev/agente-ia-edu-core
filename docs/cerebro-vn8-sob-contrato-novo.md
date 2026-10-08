# CÉREBRO — V-N8 sob o contrato de contribuição complementar

**O rótulo histórico não é alterado.** Este documento propõe, em
separado, o rótulo que o contrato novo esperaria — e registra os dois
lado a lado, com a regra sob a qual cada um vale.

| | |
|---|---|
| `FID-2FE1F9` — rótulo histórico | **`MISATTRIBUTED`** |
| contrato sob o qual foi dado | evidência atribuída apresentada como **bloco único**; a pergunta era se *aquela evidência* sustentava a afirmação |
| rótulo esperado pelo contrato novo | **`DERIVED`** |
| contrato novo | contribuição **complementar**: a unidade de suporte é a **afirmação**; cada evidência citada precisa de contribuição própria e verificável, não de sustentar sozinha o todo |

## Os fatos do caso

A afirmação: *"A entalpia de formação do metano é aproximadamente
−75 kJ/mol, obtida pela lei de Hess a partir das entalpias de combustão
do carbono (−394), do hidrogênio (−286) e do metano (−891)."*

Evidências citadas: `[E4]` e `[E5]`.

| | −394 | −286 | −891 | "Hess" |
|---|---|---|---|---|
| `E5` p.240 | **sim** | sim | sim | não |
| `E4` p.88 | **não** | sim | sim | **sim** |

## Por que o rótulo histórico está certo sob o contrato antigo

A planilha apresentava `EVIDENCIA_ATRIBUIDA` como um bloco, e a pergunta
era se **aquela evidência** sustentava a afirmação. Lida assim, a
resposta correta é a que o adjudicador deu: o bloco não contém todos os
dados da derivação, e parte do suporte está noutra evidência.

Nada nessa leitura era frouxo. Era **outra pergunta**.

## Como o contrato novo o interpretaria

Sob contribuição complementar, cada evidência precisa de **um span
próprio e verificável** — não de carregar a afirmação inteira.

Expresso no formato estruturado, o caso ficaria:

```json
{"kind": "FACTUAL",
 "text": "A entalpia de formação do metano é aproximadamente −75 kJ/mol.",
 "derivation": {
   "expression": "(-394 + 2*(-286)) - (-891)",
   "result": "-75", "unit": "kJ/mol",
   "inputs": [
     {"evidence": "E5", "span": "−394", "role": "combustão do carbono"},
     {"evidence": "E5", "span": "−286", "role": "combustão do hidrogênio"},
     {"evidence": "E5", "span": "−891", "role": "combustão do metano"},
     {"evidence": "E4", "span": "lei de Hess", "role": "nomeia o método"}
   ]}}
```

Verificação determinística esperada:

| verificação | resultado esperado | por quê |
|---|---|---|
| spans de `E5` | `SPAN_VERIFIED` ×3 | os três números estão na p.240 |
| span de `E4` | `SPAN_VERIFIED` | "lei de Hess" está na p.88 |
| evidências sem contribuição | **nenhuma** | as duas têm span próprio |
| derivação | `DERIVATION_VERIFIED` | `(-394 + 2*(-286)) - (-891) = -75` |
| entrega | **entregável** | nenhum bloqueio |

**`E4` deixa de ser co-citação indevida e passa a ser contribuição
nomeada: o método.** É precisamente o que a Decisão 1 autoriza.

## O que isto não autoriza

**Não altero implementação por causa deste caso.** Um caso não é
evidência de população, e usar o único exemplo disponível para ajustar
o verificador seria calibrar olhando o resultado — o erro que a Fase 6
recusou cometer com o Evaluation Set.

**`FID-2FE1F9` sai da regressão de `MISATTRIBUTED`** e fica marcado como
**divergente por mudança de contrato**, aguardando nova adjudicação sob
a regra complementar. Os outros 8 casos permanecem válidos: nenhum deles
depende da leitura conjuntiva estrita.

## O que ficou por confirmar

O experimento pareado **não exercitou** a derivação: nas seis execuções,
nenhuma foi produzida, e em N8 os dois caminhos declararam insuficiência
sobre um contexto que continha os dados.

Então o `DERIVED` proposto acima é o **rótulo que o contrato novo
prescreve**, verificado em teste unitário com os números reais — não um
resultado observado numa chamada real. A distinção importa e fica
registrada.
