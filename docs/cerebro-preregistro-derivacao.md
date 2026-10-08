# CÉREBRO — pré-registro da observação de derivação

Registrado **antes** de executar. Item 1 do fechamento do MVP v1.

## O que falta observar

Em 24 execuções reais acumuladas, **nenhuma derivação foi produzida**.
`DERIVATION_VERIFIED` só existe em teste unitário, com os números reais
de N8. O fechamento exige ao menos uma observação real.

## A pergunta, fixada agora

> Como se calcula a entalpia de formação de uma substância a partir das
> entalpias de combustão? Dê um exemplo numérico.

Maior sobreposição lexical: **0,182** contra
`"Qual é a entalpia de formação do metano, em kJ/mol?"` — abaixo de 0,25,
e não é paráfrase de nenhuma das 25 congeladas nem das 6 já usadas.

## Por que uma pergunta de cálculo, e não uma qualquer

**É escolha deliberada e preciso dizê-la.** O objetivo é exercitar o
mecanismo de derivação. Uma pergunta que não pede cálculo não testaria
nada — não seria um teste mais honesto, seria um teste vazio.

O que *seria* desonesto é rodar várias e reportar a que funcionou. Daí a
regra abaixo.

## Regras desta execução

1. **Uma única chamada.** Sem repetição.
2. **O resultado é o que sair.** Se o modelo recusar, não produzir
   derivação, ou produzir uma que falhe a verificação, esse é o
   resultado reportado.
3. **Nenhum ajuste de prompt, normalização, gramática ou parâmetro**
   antes, durante ou depois, em função do que sair.
4. **Se falhar, não troco a pergunta e tento de novo.** Registro a falha
   e o fechamento do MVP v1 passa a declarar que a derivação continua
   sem observação real.

## Resultados possíveis, todos aceitáveis como registro

| | o que significaria |
|---|---|
| `DERIVATION_VERIFIED` | o mecanismo funciona ponta a ponta em chamada real |
| `DERIVATION_MISMATCH` | o modelo declarou conta e resultado incoerentes — o verificador pegou |
| `DERIVATION_NOT_MECHANIZED` | expressão fora da gramática; vai a revisão humana |
| nenhuma derivação | o modelo respondeu por texto; mecanismo segue sem observação |
| `SUFFICIENCY_DENIED` | o modelo recusou; igual a N8, sem observação |

Nenhum destes é "falha do experimento". O experimento é a observação.
