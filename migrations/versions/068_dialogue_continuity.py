"""A conversa sobrevive ao recarregar: a fala do aluno, e o pedido de ajuda.

Revision ID: 068_dialogue_continuity
Revises: 067_guided_practice

ADITIVA. Duas colunas novas e uma trava nova. Nenhuma coluna existente muda,
nenhum dado e apagado, nenhuma trava existente e removida.

POR QUE UMA MIGRATION, DEPOIS DE DOIS BLOCOS EVITANDO-AS
=========================================================
Os blocos anteriores mantiveram zero migration e declararam o custo: ao
recarregar a pagina, o "15" que o aluno escreveu e a frase da hipotese se
perdiam, porque `guided_practice_items` nao tinha onde guardar a resposta.

Reauditei antes de aceitar a coluna, e a conclusao e que o resto do estado
pedagogico JA e persistido ou JA e derivavel:

    qual etapa esta de pe      linha por (aluno, item_key)   ja gravado
    quantas tentativas         attempts                      ja gravado
    resolveu sem ajuda         solved_unaided                ja gravado
    a micro-habilidade         skill                         ja gravado
    resposta normalizada       `normalizar(texto)`           derivavel
    observacao pedagogica      `observar(normalizada)`       derivavel
    hipotese e seu estado      `hipotese_para` + `atualizar` derivavel

Sobrou exatamente UM fato nao derivavel: o TEXTO que o aluno escreveu.
Nenhuma funcao pura o reconstroi, porque ele nao e consequencia de nada - ele
e a entrada. Entao a coluna existe por necessidade, e so ela.

E E SO O TEXTO - nao um deposito de estado derivado. Gravar a observacao ou a
hipotese ao lado dela criaria uma segunda fonte de verdade para algo
recalculavel, e as duas divergiriam no primeiro ajuste do conteudo curado.

`help_requests` E O SEGUNDO FATO NAO DERIVAVEL
===============================================
"Nao sei" nao gasta tentativa - cobra-la faria o retorno escalar de pista
para regra aplicada sem que o aluno tivesse tentado uma vez. A consequencia,
declarada no bloco anterior, era que o pedido de ajuda nao ficava em lugar
nenhum: sumia junto com o turno.

Isso nao e so perda de historico. Sem ele, quem disse "nao sei" e acertou na
tentativa seguinte era gravado com `solved_unaided = true` - "resolveu
sozinho" para quem pediu ajuda primeiro. A trava nova fecha isso no BANCO, e
nao so no servico:

    NOT solved_unaided OR help_requests = 0

A trava e ADITIVA tambem no sentido que importa: a coluna nasce com
`server_default 0`, entao toda linha que ja existe satisfaz a condicao, e
nenhuma precisa ser corrigida.

`hints_used` NAO FOI REAPROVEITADA de proposito: ela conta os niveis de dica
da pratica guiada, que sao quatro e escalam. Um pedido de ajuda na
investigacao nao e um nivel de dica, e somar os dois na mesma coluna faria
"pediu ajuda uma vez" e "chegou ao nivel 1 da dica" ficarem
indistinguiveis - duas coisas que pedem respostas pedagogicas diferentes.

400 CARACTERES
===============
O mesmo teto que a API ja impoe em `_RespostaAbertaRequest`. Uma resposta a
uma micropergunta e um numero ou uma frase curta; cortar em 400 nao perde
resposta real, e nao deixa a coluna virar deposito de texto livre.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "068_dialogue_continuity"
down_revision = "067_guided_practice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # O QUE O ALUNO ESCREVEU. Nulo em toda linha que ja existe, e nulo
    # tambem quando nao houve resposta lida - ambiguidade e ausencia nao
    # geram fala, e inventar uma seria pior que admitir que nao se leu.
    op.add_column(
        "guided_practice_items",
        sa.Column("response_text", sa.String(length=400), nullable=True))

    op.add_column(
        "guided_practice_items",
        sa.Column("help_requests", sa.Integer(), nullable=False,
                  server_default="0"))

    op.create_check_constraint(
        "ck_guided_practice_help_requests",
        "guided_practice_items",
        "help_requests >= 0")

    # A trava que o servico sozinho nao garantiria.
    op.create_check_constraint(
        "ck_guided_practice_unaided_has_no_help",
        "guided_practice_items",
        "NOT solved_unaided OR help_requests = 0")


def downgrade() -> None:
    op.drop_constraint("ck_guided_practice_unaided_has_no_help",
                       "guided_practice_items", type_="check")
    op.drop_constraint("ck_guided_practice_help_requests",
                       "guided_practice_items", type_="check")
    op.drop_column("guided_practice_items", "help_requests")
    op.drop_column("guided_practice_items", "response_text")
