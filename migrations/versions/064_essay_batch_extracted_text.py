"""essay_batch_pages ganha extracted_pdf_text, pra pular OCR por visao no
corpo da redacao quando o PDF enviado ja tem uma camada de texto digital
real. Isso nunca existe em foto/scan de papel fisico (uma pagina fotografada
e so pixels) - so em PDF gerado/digitado, como os PDFs sinteticos usados pra
testar o envio em lote. services/essay_batch.py::_expand_to_page_images ja
descartava esse texto (_split_pdf_pages sempre extraia, so ninguem guardava);
agora ele sobrevive ate o processamento em segundo plano, que precisa dele
porque o PDF original ja foi apagado a essa altura (limpeza do tmp_dir da
rota, spec s7).

Puramente aditiva: coluna nova, nullable, sem default necessario - toda
pagina existente hoje fica com extracted_pdf_text NULL, o que o codigo trata
exatamente como "sem camada de texto, roda OCR normal".

Revision ID: 064_essay_batch_extracted_text
Revises: 063_platform_material_target
"""

from alembic import op
import sqlalchemy as sa

revision = "064_essay_batch_extracted_text"
down_revision = "063_platform_material_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_batch_pages", sa.Column("extracted_pdf_text", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("essay_batch_pages", "extracted_pdf_text")
