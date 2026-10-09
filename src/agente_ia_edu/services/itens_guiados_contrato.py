"""O CONTRATO DA PRATICA GUIADA - o formato, sem o conteudo.

POR QUE ISTO E UM MODULO SEPARADO
==================================
Os niveis de ajuda viviam em `itens_guiados`, junto dos itens de
balanceamento. Quando Estequiometria ganhou itens proprios, o registro
(`itens_guiados`) passou a importar o conteudo de cada disciplina, e o
conteudo precisava importar os niveis de volta - um ciclo.

Entao o CONTRATO saiu na frente. Quem escreve conteudo novo importa daqui;
`itens_guiados` reexporta para nao quebrar quem ja importava de la.

OS QUATRO NIVEIS, E A DIFERENCA ENTRE AJUDA E RESPOSTA
=======================================================
Uma "dica" que diz qual alternativa marcar nao ensina - encerra. O aluno sai
com a questao certa e a duvida intacta.

Cada nivel reduz uma dificuldade DIFERENTE:

    1 CONCEITO     lembrar a ideia que vale
    2 ONDE_OLHAR   dizer por onde comecar
    3 OPERACAO     dizer que conta fazer
    4 ASSISTIDA    descrever o alvo - ainda sem apontar qual e

Ate o ultimo nivel o aluno continua tendo de identificar a alternativa. Ha
teste, por conteudo, exigindo que nenhum nivel cite a letra correta nem
reproduza o texto dela.
"""

from __future__ import annotations

AJUDA_CONCEITO = "CONCEITO"
AJUDA_ONDE_OLHAR = "ONDE_OLHAR"
AJUDA_OPERACAO = "OPERACAO"
AJUDA_ASSISTIDA = "ASSISTIDA"

NIVEIS_DE_AJUDA = (AJUDA_CONCEITO, AJUDA_ONDE_OLHAR, AJUDA_OPERACAO,
                   AJUDA_ASSISTIDA)

__all__ = ["AJUDA_ASSISTIDA", "AJUDA_CONCEITO", "AJUDA_ONDE_OLHAR",
           "AJUDA_OPERACAO", "NIVEIS_DE_AJUDA"]
