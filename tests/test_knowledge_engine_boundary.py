"""CEREBRO / Knowledge Engine - Fase 1: a fronteira do subsistema.

O Knowledge Pack e a UNICA interface publica do Knowledge Engine. Nenhum
consumidor do AGENTE IA EDU le chunk, vetor ou termo: le o Pack.

Isto nao e uma convencao para lembrar em revisao de codigo - e um teste. Ele
nasce verde (na Fase 1 nao existe consumidor algum) e o seu trabalho e falhar
no dia em que alguem, daqui a meses, importar ``KnowledgeChunk`` de dentro de
um service de trilha ou de um portal para "so dar uma olhadinha no texto".

Razoes, na ordem em que importam:

1. A politica de direitos vive na fronteira. Se um consumidor pudesse ler
   ``KnowledgeChunk.raw_text``, a trava de conteudo comercial passaria a
   depender de cada consumidor se comportar.
2. Chunk e decisao de recuperacao, nao conceito de dominio. Mudar a janela de
   chunking ou o algoritmo de fusao nao pode quebrar consumidor nenhum.
3. Um chunk solto nao carrega proveniencia nem forca de evidencia; o Pack
   carrega as duas.

Mesmo recurso que ``FORBIDDEN_COLUMN_FRAGMENTS`` ja usa em
``tests/test_r0_identity_models.py``: uma regra estrutural conferida por
varredura do proprio codigo-fonte.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"

#: Modelos internos do corpus. O Pack e publico; estes nao sao.
INTERNAL_MODELS = frozenset(
    {
        "KnowledgeSource",
        "KnowledgeDocument",
        "KnowledgeChunk",
        "KnowledgeChunkTerm",
        "KnowledgeEmbeddingSpace",
        "KnowledgeChunkEmbedding",
        # Fase 5: o estado do indice lexical e tao interno quanto os postings.
        # Um consumidor que lesse "esta indexado?" passaria a depender do
        # mecanismo de busca, que e justamente o que a fronteira protege -
        # o backend lexical e substituivel (spec 23.1).
        "KnowledgeChunkLexicalIndex",
        "KnowledgeLexicalIndexState",
    }
)

#: Quem pode, legitimamente, tocar os modelos internos.
#: - ``db/models/*``        : o registro de modelos; e onde eles sao definidos.
#: - ``services/knowledge_engine/*``: o proprio subsistema.
#: - ``repositories/knowledge_engine*``: consultas do proprio subsistema, se
#:   um dia forem extraidas, seguindo o que o projeto ja faz em
#:   ``repositories/``.
ALLOWED_PREFIXES = (
    "db/models/",
    "services/knowledge_engine/",
    "repositories/knowledge_engine",
)

#: O modelo de CURADORIA tem a fronteira inversa: pertence ao dominio de
#: curriculo, e o Knowledge Engine so o alcanca pelo PORT
#: (``curriculum_ports``), nunca de dentro do chunker, do retriever ou do
#: pack_builder. O Pack ve ``BnccReference``; a tabela e detalhe que ele nao
#: deve nomear (spec 22.8).
CURATION_MODELS = frozenset({"CurriculumBnccLink", "CurriculumBnccLinkReview"})
CURATION_ALLOWED_PREFIXES = (
    "db/models/",
    "services/curriculum_bncc_links.py",
    "services/knowledge_engine/curriculum_ports.py",
    "api/routes/curriculum_bncc.py",
)


def _relative_posix(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def _is_allowed(relative: str) -> bool:
    return any(relative.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _imported_names(tree: ast.AST) -> set[str]:
    """Todo nome trazido por ``from ... import X`` ou ``import ... as X``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.rsplit(".", 1)[-1])
    return names


class KnowledgeEngineBoundaryTests(unittest.TestCase):
    def test_source_tree_is_where_we_think_it_is(self):
        """Guarda contra um teste que passa por nao ter varrido nada."""
        self.assertTrue(SOURCE_ROOT.is_dir(), SOURCE_ROOT)
        self.assertTrue(
            (SOURCE_ROOT / "db" / "models" / "knowledge_engine.py").is_file(),
            "o modulo de modelos do Knowledge Engine deveria existir",
        )

    def test_no_module_outside_the_subsystem_imports_the_internal_models(self):
        offenders: list[str] = []
        scanned = 0
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = _relative_posix(path)
            scanned += 1
            if _is_allowed(relative):
                continue
            leaked = _imported_names(ast.parse(path.read_text(), filename=str(path)))
            leaked &= INTERNAL_MODELS
            if leaked:
                offenders.append(f"{relative} importa {sorted(leaked)}")

        self.assertGreater(scanned, 50, "a varredura nao encontrou codigo suficiente")
        self.assertEqual(
            offenders,
            [],
            "O Knowledge Pack e a unica interface publica do Knowledge Engine. "
            "Estes modulos leem o corpus diretamente:\n  " + "\n  ".join(offenders),
        )

    def test_the_guard_would_actually_catch_a_violation(self):
        """Um teste de fronteira que nao consegue falhar nao protege nada."""
        fake = ast.parse("from agente_ia_edu.db.models import KnowledgeChunk\n")
        self.assertEqual(_imported_names(fake) & INTERNAL_MODELS, {"KnowledgeChunk"})
        self.assertFalse(_is_allowed("services/learning_path.py"))
        self.assertTrue(_is_allowed("services/knowledge_engine/retrieval.py"))
        self.assertTrue(_is_allowed("db/models/knowledge_engine.py"))

    def test_only_the_port_and_the_curation_service_touch_the_link_model(self):
        """O Knowledge Pack vera ``BnccReference``, nunca a tabela de
        curadoria. Se o chunker ou o futuro pack_builder importarem o modelo,
        a fronteira do spec 22.8 foi perdida."""
        offenders: list[str] = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = _relative_posix(path)
            if any(relative.startswith(prefix) for prefix in CURATION_ALLOWED_PREFIXES):
                continue
            leaked = _imported_names(
                ast.parse(path.read_text(), filename=str(path))
            ) & CURATION_MODELS
            if leaked:
                offenders.append(f"{relative} importa {sorted(leaked)}")
        self.assertEqual(
            offenders,
            [],
            "a curadoria curriculo<->BNCC e alcancada pelo port, nunca pelo "
            "modelo:\n  " + "\n  ".join(offenders),
        )

    def test_the_legacy_knowledge_service_name_is_not_reused(self):
        """``services/knowledge.py :: KnowledgeService`` ja existe e e outra
        coisa (consulta relacional do catalogo). O subsistema novo nunca pode
        reusar esse nome."""
        subsystem = SOURCE_ROOT / "services" / "knowledge_engine"
        if not subsystem.is_dir():
            self.skipTest("o pacote de services do Knowledge Engine ainda nao existe (Fase 2+)")
        for path in subsystem.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    self.assertNotEqual(
                        node.name,
                        "KnowledgeService",
                        f"{_relative_posix(path)} colide com services/knowledge.py",
                    )


if __name__ == "__main__":
    unittest.main()
