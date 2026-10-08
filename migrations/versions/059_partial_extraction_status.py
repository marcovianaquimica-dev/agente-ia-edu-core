"""CEREBRO / Knowledge Engine - estado PARTIAL de extracao (Fase 3).

Revision ID: 059_partial_extraction_status
Revises: 058_knowledge_engine_embeddings

Acrescenta ``PARTIAL`` ao CheckConstraint de
``knowledge_documents.extraction_status``.

POR QUE UM ESTADO PROPRIO. "Extraido em parte" nao e nem EXTRACTED nem
FAILED, e colapsa-lo em qualquer dos dois perde a informacao que mais
importa. Escondido, e exatamente o estado que produz um corpus em que as
pessoas confiam mais do que deveriam: alguem pede "Estequiometria", recebe um
Knowledge Pack magro, e nao tem como saber que tres paginas do capitulo nunca
foram lidas.

Os critérios que levam a PARTIAL sao deterministas e estao na secao 20.3 da
spec. Pagina vazia NAO e, por si, perda de conteudo - paginas podem ser
intencionalmente vazias ou predominantemente visuais.

DROP + ADD de CHECK no PostgreSQL: rapido, sem reescrita de tabela. No SQLite
a alteracao de CheckConstraint exigiria recriar a tabela, e nao ha ganho em
faze-lo: os testes SQLite constroem o schema por ``Base.metadata.create_all``
a partir do modelo, que ja traz o valor novo.
"""

from alembic import op

# ATENCAO: ``alembic_version.version_num`` e VARCHAR(32). Um id mais longo
# que isso NAO falha ao escrever a migracao - falha ao APLICA-LA, com
# StringDataRightTruncation, e so nos testes que exercitam a cadeia Alembic.
# A primeira versao deste arquivo usava
# "059_knowledge_documents_partial_status" (38 chars) e quebrou 46 testes em
# quatro arquivos *_postgresql.
revision = "059_partial_extraction_status"
down_revision = "058_knowledge_engine_embeddings"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_knowledge_documents_extraction_status"
_WITH_PARTIAL = (
    "extraction_status IN ('PENDING', 'EXTRACTING', 'EXTRACTED', 'PARTIAL', 'FAILED')"
)
_WITHOUT_PARTIAL = "extraction_status IN ('PENDING', 'EXTRACTING', 'EXTRACTED', 'FAILED')"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, "knowledge_documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "knowledge_documents", _WITH_PARTIAL)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Uma linha ja em PARTIAL impediria o downgrade, e isso e correto: baixar
    # a constraint com dados que a violam e que seria o erro.
    op.drop_constraint(_CONSTRAINT, "knowledge_documents", type_="check")
    op.create_check_constraint(_CONSTRAINT, "knowledge_documents", _WITHOUT_PARTIAL)
