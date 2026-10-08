# CÉREBRO — Etapa A: medir `sufficient` antes de construir o portão

Três execuções reais pré-registradas, rodadas **antes** de qualquer
mudança de comportamento. A pergunta: `sufficient: false` aparece
espontaneamente quando o corpus tem graus medidos de ausência?

Textos das perguntas **não alterados**. Comportamento idêntico entre as
três: mesmo laço, mesmos parâmetros, nenhum ajuste entre uma e outra.
Nenhum ajuste de prompt para obter resultado desejado.

## Minha previsão estava errada

Eu havia registrado, antes de rodar: *"provavelmente nenhuma das três
abstém"*. **Errado.** O sinal não é inerte.

| | `sufficient` bruto | status | `is_grounded` |
|---|---|---|---|
| **N8** entalpia de formação do metano | **`True`** | `GROUNDED` | `True` |
| **N9** hibridização sp³ | **`False`** | `GROUNDED` | `True` |
| **N10** Diels-Alder | **`False`** | `GROUNDED` | `True` |

Primeiras ocorrências de `sufficient: false` na história do projeto — em 9
execuções reais acumuladas, as 6 anteriores todas `True`.

**E os três acertaram.** Em N9 e N10 o material realmente não bastava; em
N8, bastava. 3/3 de concordância com a realidade, verificada abaixo.

## Condições (idênticas nas três)

| | |
|---|---|
| fingerprint | `8471ea331d464904` (único nas três) |
| cobertura | 5.911/5.911, 0 faltando, 0 obsoletos |
| `candidate_cap` | 2000, atingido nas três |
| filtrados por `EDITORIAL_ROLE` | 239 nas três |
| degradado | `False` nas três |
| `retrieval_purpose` | `LEARN` |
| `policy` / `normalizer` / `detector` | v1 / v1 / v1 |

## Resultados

| | N8 | N9 | N10 |
|---|---|---|---|
| score máx → mín | 0,5811 → 0,5432 | 0,4841 → 0,4336 | 0,5196 → 0,5013 |
| amplitude | 0,0379 | 0,0505 | **0,0183** |
| `CONTENT` / `ANSWER_KEY` | 7 / 3 | 9 / 1 | 8 / 2 |
| fontes distintas | 3 | 3 | **2** |
| evidências no contexto | 7 | 7 | **4** |
| chars usados | 10.260 | 9.583 | 11.074 |
| `BUDGET_EXHAUSTED` | 3 | 3 | **6** |
| prompt (tokens) | 3.798 | 2.783 | 3.263 |
| saída (tokens) | 521 | 163 | 333 |
| total (ms) | 11.045 | 5.817 | 5.761 |
| custo | US$ 0,00088268 | US$ 0,00051551 | US$ 0,00068971 |

**Custo total das três: US$ 0,00208790.**

N9 tem os menores scores já observados em qualquer execução real
(0,4841 no topo) — coerente com um conceito que não existe no corpus.

## N8 — o caso que reescreve o próprio teste

> **Resposta:** A entalpia de formação do metano é aproximadamente
> **−75 kJ/mol**, obtida pela lei de Hess a partir das entalpias de
> combustão do carbono (−394 kJ/mol), do hidrogênio (−286 kJ/mol) e do
> metano (−891 kJ/mol): ΔHf(CH₄) = [−394 + 2(−286)] − (−891) = −75 kJ/mol.
> [E5]

Isso **parece** alucinação bem formatada: o valor não existe no corpus e o
modelo produziu um número com uma citação. Fui verificar antes de
concluir.

Verificação programática sobre os textos efetivamente enviados, sem
imprimir literal:

| | 394 | 286 | 891 | 75 |
|---|---|---|---|---|
| **[E5]**, a única citada | **sim** | **sim** | **sim** | não |
| conjunto inteiro do contexto | sim | sim | sim | **AUSENTE** |

**Os três insumos estão no chunk citado. O resultado não está em lugar
nenhum.** O modelo leu três entalpias de combustão da fonte que citou e
aplicou a lei de Hess. O valor confere com a literatura (−74,6 kJ/mol).

Isso é **raciocínio fundamentado legítimo**, não fabricação. A citação
sustenta o que foi afirmado.

### A lição metodológica, que é minha

Eu projetei N8 como teste de abstenção porque a string *"entalpia de
formação do metano"* tem 0 ocorrências no corpus. **Ausência lexical não é
ausência informacional.** Os ingredientes estavam lá, derivar era possível,
e minha triagem por `ILIKE` não tinha como ver isso.

N8 não mediu abstenção. Mediu outra coisa, mais interessante: que o
sistema **deriva** em vez de só recuperar, e que `sufficient: True` estava
correto ao dizer que o material bastava.

Fica registrado como limite do método de triagem, não como defeito do
sistema.

## N9 e N10 — a recusa funciona, e sai como resposta fundamentada

> **N9:** As evidências não bastam para definir completamente a
> hibridização sp³, pois não explicam o significado de "sp³". Elas
> informam que um átomo de carbono que estabelece quatro ligações simples
> apresenta geometria tetraédrica com seus ligantes. [E6]

> **N10:** Não é possível determinar o mecanismo da reação de Diels-Alder
> nem sua estereoquímica com as evidências fornecidas. Elas tratam
> genericamente de reações de adição [E4] e de alguns casos de
> estereoisomeria [E3], mas não descrevem especificamente a reação de
> Diels-Alder.

As duas recusam **e citam corretamente** — N10 usa a citação de modo
*negativo*, para mostrar o que a evidência cobre e o que não cobre. Os
marcadores conferem, nenhum inválido.

**E as duas saem hoje como `GROUNDED`, `is_grounded = True`.**

É exatamente o buraco, agora demonstrado com dado real: uma recusa
explícita do modelo é publicada pelo sistema como resposta fundamentada.
Não é hipótese de desenho — aconteceu duas vezes em três chamadas.

## `used_evidence` veio com colchetes nas TRÊS

| | bruto | normalizado |
|---|---|---|
| N8 | `['[E5]']` | `['E5']` |
| N9 | `['[E6]']` | `['E6']` |
| N10 | `['[E3]', '[E4]']` | `['E4', 'E3']` |

**3 de 3.** A correção de normalização não é um conserto de caso raro: ela
atua em toda chamada real. Sem ela, as três teriam saído
`INVALID_EVIDENCE_REFERENCE`.

Isso fecha a ambiguidade deixada pela segunda execução real, que não
conseguiu provar a correção porque o relatório só guardava a forma
normalizada. Aqui o bruto foi preservado — e é por isso que esta etapa
exigia preservá-lo.

## O que estes dados dizem sobre o portão

**A favor de `DENIED` bloquear a entrega:**

- o sinal **existe** e aparece sem indução;
- nas duas ocorrências ele estava **certo**;
- o texto produzido junto é uma recusa — bloqueá-lo não perde resposta,
  e ainda evita informar ao aluno o que o acervo contém ou não.

**Contra confiar nele cegamente:**

- n = 2 ocorrências de `DENIED`, n = 9 execuções no total;
- N9 recusou **e mesmo assim entregou conteúdo correto** ("geometria
  tetraédrica… [E6]"). Bloquear descarta esse fragmento útil;
- nada garante que o modelo negue quando deveria em outros formatos de
  pergunta.

**Decisão documentada:** `DENIED` passa a bloquear a entrega, **e** toda
ocorrência é marcada para revisão humana. O bloqueio é a leitura
conservadora de uma afirmação explícita; a marcação é o reconhecimento de
que duas observações não fazem uma taxa de erro.

Nenhum prompt foi ajustado para forçar o sinal, e nenhum será.

## Dívida registrada aqui

**O relatório de hoje não guarda `used_evidence` bruto nem `sufficient`
bruto.** Esta etapa só conseguiu medir porque usei um gravador em volta do
provider, fora do caminho padrão. A Etapa B paga essa dívida.
