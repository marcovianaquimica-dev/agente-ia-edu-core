"""Perfil Aluno - a rota pedagogica que levou o aluno ate a tarefa.

Revision ID: 064_study_session_readiness
Revises: 063_embedding_activation

ADITIVA. Tres colunas em ``study_sessions``. Nenhuma coluna existente muda,
nenhuma linha existente e reescrita.

POR QUE ISTO E UMA COLUNA E NAO UM CAMPO NO JSON
================================================

``study_sessions`` ja tem ``plan`` e ``metadata`` em JSON, e seria mais rapido
enfiar a rota ali. Nao e o que o dado pede.

A pergunta que Professor e Coordenacao vao fazer e de AGREGACAO, sobre a
turma inteira: "quantos alunos ainda nao realizaram a tarefa de
Estequiometria, e destes, quantos estao se PREPARANDO para ela?". Isso e um
GROUP BY sobre dezenas de milhares de sessoes. Em JSON vira varredura; em
coluna, com indice, e uma consulta comum.

A distincao importa pedagogicamente. Hoje "nao fez a tarefa" e um unico
balde, e dentro dele moram dois alunos muito diferentes:

    o que nao abriu a atividade
    o que abriu, descobriu que faltava um pre-requisito, e esta estudando ele

Tratar os dois igual e o erro que esta migration existe para impedir.

E IRREVERSIVEL NO SENTIDO QUE IMPORTA
=====================================

Nao em DDL - ``downgrade`` devolve a tabela ao estado anterior. Irreversivel
no HISTORICO: a rota so pode ser gravada no instante em que a sessao e
montada, porque ela e o resultado de uma decisao tomada sobre o estado de
dominio *daquele momento*. Nao da para reconstruir depois a partir das
sessoes antigas. Cada mes sem estas colunas e um mes de dado que nao existe.

A TRAVA
=======

CheckConstraint com os tres valores, em vez de String livre. O conjunto e
fechado por definicao - sao os tres caminhos de prontidao do planejador - e
um quarto valor so deve entrar junto com a decisao de produto que o criou,
nao por um typo em codigo de aplicacao. NULL continua valido: sessao montada
antes desta migration, ou por um caminho que nao avalia prontidao.
"""

import sqlalchemy as sa
from alembic import op

revision = "064_study_session_readiness"
down_revision = "063_embedding_activation"
branch_labels = None
depends_on = None


ROTAS = ("DIRECT", "DIAGNOSTIC", "PREREQUISITE_PREPARATION")


def upgrade() -> None:
    op.add_column(
        "study_sessions",
        sa.Column("readiness_route", sa.String(30), nullable=True),
    )
    op.add_column(
        "study_sessions",
        # Sem ForeignKey de proposito: a sessao e um registro HISTORICO do que
        # o aluno fez. Se a atividade for apagada, a sessao nao pode sumir
        # junto nem travar a exclusao - o fato de que ele estudou continua
        # verdadeiro. Mesma razao pela qual student_external_id e String.
        sa.Column("objective_assignment_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "study_sessions",
        sa.Column(
            "objective_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_study_sessions_readiness_route",
        "study_sessions",
        sa.text(
            "readiness_route IS NULL OR readiness_route IN "
            "('DIRECT', 'DIAGNOSTIC', 'PREREQUISITE_PREPARATION')"
        ),
    )
    op.create_index(
        # A consulta do Professor: desta atividade, quem chegou por qual rota
        # e quem concluiu. As tres colunas na ordem em que ele filtra.
        "ix_study_sessions_objective_route",
        "study_sessions",
        ["objective_assignment_id", "readiness_route", "objective_completed"],
        postgresql_where=sa.text("objective_assignment_id IS NOT NULL"),
        sqlite_where=sa.text("objective_assignment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_study_sessions_objective_route", table_name="study_sessions"
    )
    op.drop_constraint(
        "ck_study_sessions_readiness_route", "study_sessions", type_="check"
    )
    op.drop_column("study_sessions", "objective_completed")
    op.drop_column("study_sessions", "objective_assignment_id")
    op.drop_column("study_sessions", "readiness_route")
