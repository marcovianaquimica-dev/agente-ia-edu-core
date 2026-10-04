"""Como uma formula quimica e ESCRITA para o aluno ler.

    2 H₂O
    ^ ^
    |  +-- INDICE (atomicidade): subscrito
    +----- COEFICIENTE (quantas moleculas): tamanho normal

Trocar todo digito por subscrito escreveria `₂ H₂O`, que diz outra coisa. Por
isso isto e uma funcao com teste, e nao um replace no JavaScript.

DE ONDE VEIO O PROBLEMA
=======================
Dos 14 itens do Nucleo Diagnostic Bank, 10 nasceram com subscrito Unicode
(`H₂ + O₂`) e 4 com formula ASCII (`H2 + O2`). O gerador nao foi instruido a
padronizar, e as tres perguntas de uma unica sessao podem misturar as duas
notacoes - foi o que o teste humano de 2026-10-04 viu na tela.

A causa esta no CONTEUDO PERSISTIDO, nao na renderizacao. Esta funcao e
aplicada na escrita (pelo seed, ao carregar o banco) e e idempotente, de modo
que rodar de novo sobre texto ja correto nao muda nada.

ONDE ELA NAO E APLICADA, DE PROPOSITO
======================================
No Player compartilhado. Ele serve questao de Matematica tambem, onde `2x2` e
`3x3` nao sao formulas, e uma regra de Quimica aplicada a todo enunciado do
sistema produziria erro silencioso em outra disciplina. O dono destes itens e
o Nucleo; a normalizacao mora com o dono.
"""

from __future__ import annotations

import re

SUBSCRITOS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

# Um indice e um numero colado DEPOIS de um simbolo de elemento ou de um
# parentese que fecha:
#
#     H2      Fe2O3      Ca(OH)2      (NH4)2SO4
#      ^         ^  ^          ^           ^  ^
#
# O simbolo exige INICIAL MAIUSCULA, como todo elemento da tabela periodica. E
# isso sozinho que protege `item2`: nao ha maiuscula imediatamente antes do
# digito, entao o padrao simplesmente nao casa - nenhum lookbehind necessario.
# (Tentei um: `(?<![A-Za-z])` barrava `SO4`, `NH4` e `Ca(OH)2`, porque nessas
# o simbolo vem colado ao anterior. O remedio era pior.)
#
# Um numero que ABRE o termo (`2 H2O`, `2H2O`) nao tem letra antes e portanto
# nao casa: e coeficiente.
_INDICE = re.compile(r"([A-Z][a-z]?|\))(\d+)(?![+\-\d])")


def subscrever(texto: str | None) -> str | None:
    """Converte INDICES para subscrito, deixando coeficientes intactos.

    Fail-closed por construcao: o que o padrao nao reconhece fica como esta.
    Texto sem subscrito e feio; texto com o subscrito no lugar errado esta
    quimicamente ERRADO, e o segundo e muito pior num diagnostico.

    O ``(?![+\\-\\d])`` final e o que protege a carga do ion: em `Fe3+` o 3 e
    carga, nao atomicidade, e `Fe₃⁺` seria outra especie quimica.
    """
    if not texto:
        return texto

    def troca(m: re.Match) -> str:
        return m.group(1) + m.group(2).translate(SUBSCRITOS)

    return _INDICE.sub(troca, texto)


__all__ = ["subscrever", "SUBSCRITOS"]
