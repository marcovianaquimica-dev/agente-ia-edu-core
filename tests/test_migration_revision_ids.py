"""Todo id de revisao do Alembic cabe em ``alembic_version.version_num``.

A tabela ``alembic_version`` guarda ``version_num`` como ``VARCHAR(32)``.

Um id mais longo que isso NAO falha ao escrever a migracao, nem ao importa-la,
nem ao rodar a maior parte da suite: falha ao APLICAR a migracao, com
``StringDataRightTruncation``, e so nos testes que exercitam a cadeia Alembic
de verdade. Em 2026-10-01 a migracao 059 nasceu com
``059_knowledge_documents_partial_status`` (38 caracteres) e derrubou 46
testes em quatro arquivos ``*_postgresql`` que nada tinham a ver com ela - um
estrago cuja causa nao aparece em nenhuma mensagem de erro proxima do arquivo
culpado.

Este teste e barato e torna o defeito impossivel de repetir.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

#: Largura de ``alembic_version.version_num``.
MAX_REVISION_ID_CHARS = 32

_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
# A anotacao de tipo e opcional de proposito: algumas migracoes do projeto
# escrevem ``revision: str = "..."`` e ``down_revision: Union[str, None] =
# "..."``. Ignora-las faria este guarda acusar uma cadeia quebrada que esta
# intacta - foi o que aconteceu na primeira versao deste arquivo.
_REVISION = re.compile(
    r"^revision(?:\s*:[^=]+)?\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE
)
_DOWN_REVISION = re.compile(
    r"^down_revision(?:\s*:[^=]+)?\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE
)


def _revision_ids() -> dict[str, str]:
    """``{id: nome do arquivo}`` para toda migracao do projeto."""
    found: dict[str, str] = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        if path.name.startswith("__"):
            continue
        match = _REVISION.search(path.read_text())
        if match:
            found[match.group(1)] = path.name
    return found


class MigrationRevisionIdTests(unittest.TestCase):
    def test_the_versions_directory_was_actually_scanned(self):
        """Guarda contra um teste que passa por nao ter olhado nada."""
        self.assertTrue(_VERSIONS.is_dir(), _VERSIONS)
        self.assertGreater(len(_revision_ids()), 40)

    def test_every_revision_id_fits_in_version_num(self):
        too_long = {
            revision: f"{len(revision)} chars em {filename}"
            for revision, filename in _revision_ids().items()
            if len(revision) > MAX_REVISION_ID_CHARS
        }
        self.assertEqual(
            too_long,
            {},
            "alembic_version.version_num e VARCHAR(32); estes ids nao cabem e "
            f"falhariam ao aplicar a migracao: {too_long}",
        )

    def test_every_down_revision_points_at_a_revision_that_exists(self):
        known = set(_revision_ids())
        dangling = []
        for path in sorted(_VERSIONS.glob("*.py")):
            if path.name.startswith("__"):
                continue
            match = _DOWN_REVISION.search(path.read_text())
            if match and match.group(1) not in known:
                dangling.append(f"{path.name} -> {match.group(1)}")
        self.assertEqual(dangling, [], f"down_revision sem destino: {dangling}")

    def test_revision_ids_are_unique(self):
        ids: list[str] = []
        for path in sorted(_VERSIONS.glob("*.py")):
            if path.name.startswith("__"):
                continue
            match = _REVISION.search(path.read_text())
            if match:
                ids.append(match.group(1))
        duplicates = {value for value in ids if ids.count(value) > 1}
        self.assertEqual(duplicates, set(), f"ids repetidos: {duplicates}")

    def test_the_guard_would_catch_an_oversized_id(self):
        """Um guarda que nao consegue falhar nao protege nada."""
        offender = "059_knowledge_documents_partial_status"
        self.assertGreater(len(offender), MAX_REVISION_ID_CHARS)


if __name__ == "__main__":
    unittest.main()
