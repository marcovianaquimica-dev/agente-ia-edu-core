
# CÉREBRO — adjudicação humana dos 9 casos de fidelidade

Pacote cego devolvido preenchido. **Integridade validada antes de olhar
qualquer número** — cabeçalhos inalterados, 9 IDs, nenhuma duplicata,
afirmação/identificação/evidência não alteradas, 9 de 9 preenchidos,
todas as classes válidas. Nenhuma falha.

Conjunto de **regressão**, não Calibration Set. O corte `coverage < 0,50`
continua heurística exploratória v1 — e os números abaixo são razão para
**não** congelá-lo.

## Resultado

| classe | n |
|---|---:|
| `MISATTRIBUTED` | **4** |
| `DIRECTLY_SUPPORTED` | 3 |
| `DERIVED` | 1 |
| `UNSUPPORTED_EXTERNAL` | 1 |
| `UNCITED_META` | 0 |

## Triagem automática × julgamento humano

| ID | caso | triagem v1 | humano | bate |
|---|---|---|---|---|
| `FID-E31182` | N2 molho de salada | externa | `UNSUPPORTED_EXTERNAL` | **sim** |
| `FID-C43E7E` | N2 funil | externa | `DIRECTLY_SUPPORTED` | não |
| `FID-AACD62` | PC colunas/grupos | má atribuição | `MISATTRIBUTED` | **sim** |
| `FID-FC5F77` | PA Thomson | má atribuição | `MISATTRIBUTED` | **sim** |
| `FID-C3F597` | PB Brønsted-Lowry | má atribuição | `MISATTRIBUTED` | **sim** |
| `FID-AAB23E` | PD observação/erro | má atribuição | `DIRECTLY_SUPPORTED` | não |
| `FID-5B9791` | PD conferência final | má atribuição | `DIRECTLY_SUPPORTED` | não |
| `FID-38738F` | A-N8 Hess | não sinalizado | `DERIVED` | **sim** |
| `FID-2FE1F9` | V-N8 Hess | não sinalizado | `MISATTRIBUTED` | não |

**Concordância: 5 de 9.**

Como detector de problema (`UNSUPPORTED_EXTERNAL` ∪ `MISATTRIBUTED`):

| | |
|---|---|
| verdadeiros positivos | 4 |
| falsos positivos | **3** |
| falsos negativos | **1** |
| precisão | **4/7 = 57%** |
| revocação | 4/5 = 80% |

## Os dois casos obrigatórios, confirmados

**N2 molho de salada → `UNSUPPORTED_EXTERNAL`.** O caso positivo se
sustenta. Nota do adjudicador: *"As evidências disponíveis tratam de
água/óleo, imiscibilidade e vinagre em outro experimento, mas não
sustentam o exemplo específico de molho de salada."*

**A-N8 Hess → `DERIVED`.** O negativo obrigatório não foi falsamente
rejeitado. Nota: *"Os valores de combustão necessários estão na
evidência; o valor de aproximadamente −75 kJ/mol resulta de aplicação
legítima da lei de Hess."*

## Os três falsos positivos são todos paráfrase

| | cobertura citada | o que o humano viu |
|---|---:|---|
| N2 funil | 0,33 | *"descreve diretamente o uso do funil de separação, o escoamento da água e a permanência do óleo"* |
| PD observação | 0,56 | *"descreve explicitamente o método por observação, tentativa e erro, com ajustes sucessivos"* |
| PD conferência | 0,43 | *"descreve o balanceamento pela igualdade do número de átomos… e faz a conferência final no exemplo"* |

O humano lê sentido; a triagem conta palavras. Quando o livro diz a mesma
coisa com outras palavras, a contagem erra. **Essa é a limitação central
da abordagem lexical, e ela custou 3 de 9.**

## O falso negativo é mais interessante que os três positivos

`V-N8` não foi sinalizado — cobertura 0,78, alta — e o humano julgou
`MISATTRIBUTED`.

Fui investigar. **Os contextos de A-N8 e V-N8 são idênticos**: mesmos 7
chunks, mesmos marcadores. A única diferença é o que o modelo citou:

| | citou | julgamento |
|---|---|---|
| A-N8 | `[E5]` | `DERIVED` |
| V-N8 | `[E4]` **e** `[E5]` | `MISATTRIBUTED` |

E os dados:

| | −394 | −286 | −891 | Hess |
|---|---|---|---|---|
| `E5` p.240 | **sim** | **sim** | **sim** | não |
| `E4` p.88 | **não** | sim | sim | sim |

`E5` carrega a derivação inteira. `E4` carrega Hess e dois dos três
valores, mas **não o −394**.

Acrescentar `E4` a uma citação que já estava correta **inverteu o
veredicto** — mesmo o conjunto citado sendo um superconjunto do que foi
julgado legítimo.

### Duas consequências

**1. A atribuição múltipla é lida de forma conjuntiva.** O adjudicador
espera que **cada** evidência citada contribua. Pela leitura disjuntiva
("basta uma sustentar"), V-N8 seria `DERIVED`.

**Isto é uma pergunta de contrato, não um detalhe de implementação.** As
duas leituras dão veredictos opostos num caso real, e a regra precisa ser
escrita antes de qualquer verificador. Não é decisão minha.

**2. Minha cobertura é calculada sobre a UNIÃO dos chunks citados.** A
completude de `E5` mascarou a lacuna de `E4`. Cobertura por união **não
consegue** detectar co-citação ruim, por construção. Calcular por
marcador resolveria este caso específico — proposta, não implementada.

## O que isto muda no desenho

**O modo de falha dominante não é o que eu tinha enquadrado.**

Formulei o problema em torno de N2 e fabricação. Nos 9 adjudicados:
**fabricação é 1; má atribuição é 4.** O sistema erra mais o *endereço* da
citação do que inventa conteúdo.

**Minha recomendação de prototipar C primeiro fica enfraquecida pelos
próprios números.** Eu a justifiquei dizendo que a triagem acertava o caso
conhecido com zero falso positivo — mas isso era sobre dados **sem
rótulo**. Com rótulo: precisão de 57%, e um falso negativo. Como
instrumento primário, não serve.

**A âncora por span (alternativa B) ataca exatamente o modo dominante.**
Se o modelo precisa citar um trecho literal do chunk que indicou,
indicar o chunk errado falha por substring — deterministicamente, sem
juiz. Os 4 casos de `MISATTRIBUTED` seriam pegos por construção, e os 3
falsos positivos de paráfrase **desapareceriam**, porque a verificação
deixaria de depender de o modelo repetir as palavras do livro: ele
escolheria o trecho que sustenta.

Isso não era argumento disponível antes da adjudicação. Agora é empírico.

## Recomendação revista

**Prototipar B. Usar C apenas como rede de segurança, nunca como
instrumento primário.**

Antes disso, duas decisões suas:

1. **Atribuição múltipla é conjuntiva ou disjuntiva?** Define o veredicto
   de V-N8 e a regra do verificador.
2. **Liberar o prompt de resposta**, sem o qual B não existe.

## Limites deste conjunto

Nove casos, um adjudicador, uma rodada. Serve como **regressão** — os
nove viram testes que devem continuar passando — e **não** como base
para calibrar limiar, escolher parâmetro ou medir taxa de erro de
população. Em particular, `coverage < 0,50` não deve ser congelado a
partir destes números; o que eles mostram é que o limiar é fraco, não
qual seria o bom.

Os 9 casos não foram adicionados a nenhum conjunto congelado, e nenhuma
alteração de código foi feita.
