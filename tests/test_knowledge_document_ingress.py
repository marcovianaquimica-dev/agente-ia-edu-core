"""CEREBRO / Knowledge Engine - Fase 2: entrada de arquivo de documento.

A Fase 2 implementa SO o caminho ``LOCAL_PATH`` - o piloto e operado por API
e scripts na mesma infraestrutura, e os livros chegam a 175 MB, que nao faz
sentido atravessarem HTTP.

Mas ``LOCAL_PATH`` nao e a interface conceitual do Cerebro. O resultado da
validacao e um ``ResolvedDocumentFile``, e o service aceita ESSE tipo, nunca
um caminho cru. Quando a tela de administracao existir, ``UPLOAD`` constroi o
mesmo tipo e nada no service, no modelo ou na idempotencia muda.

Estes testes cobrem a validacao do caminho, que e a superficie de risco da
opcao escolhida.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agente_ia_edu.services.knowledge_engine.document_ingress import (
    ALLOWED_SUFFIXES,
    DocumentIngressError,
    ResolvedDocumentFile,
    configured_document_root,
    configured_max_document_bytes,
    resolve_local_path,
)


class _RootedCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # resolve(): em macOS /tmp e symlink para /private/tmp, e uma raiz nao
        # resolvida faria todo caminho legitimo parecer estar fora dela.
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, relative: str, content: bytes = b"%PDF-1.7 conteudo") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


class HappyPathTests(_RootedCase):
    def test_a_file_inside_the_root_resolves(self):
        self._write("livros/quimica.pdf")
        resolved = resolve_local_path("livros/quimica.pdf", root=self.root)
        self.assertIsInstance(resolved, ResolvedDocumentFile)
        self.assertEqual(resolved.original_filename, "quimica.pdf")
        self.assertEqual(resolved.mime_type, "application/pdf")
        self.assertEqual(resolved.ingress, "LOCAL_PATH")
        self.assertTrue(resolved.path.is_absolute())
        self.assertTrue(resolved.path.is_relative_to(self.root))

    def test_an_absolute_path_inside_the_root_also_resolves(self):
        written = self._write("a.md", b"# titulo")
        resolved = resolve_local_path(str(written), root=self.root)
        self.assertEqual(resolved.path, written.resolve())
        self.assertEqual(resolved.size_bytes, len(b"# titulo"))

    def test_every_allowed_suffix_is_accepted(self):
        for suffix in ALLOWED_SUFFIXES:
            self._write(f"arquivo{suffix}")
            resolved = resolve_local_path(f"arquivo{suffix}", root=self.root)
            self.assertEqual(resolved.path.suffix, suffix)

    def test_the_suffix_check_is_case_insensitive(self):
        self._write("LIVRO.PDF")
        self.assertEqual(
            resolve_local_path("LIVRO.PDF", root=self.root).mime_type, "application/pdf"
        )


class EscapeAttemptTests(_RootedCase):
    def test_a_parent_traversal_is_rejected(self):
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("../etc/passwd.pdf", root=self.root)
        self.assertEqual(caught.exception.code, "PATH_OUTSIDE_ROOT")

    def test_a_traversal_that_loops_back_inside_is_still_evaluated_on_the_resolved_path(self):
        """``a/../b.pdf`` resolve para dentro da raiz, entao e legitimo.

        A regra e sobre o caminho RESOLVIDO, nao sobre a presenca de '..' no
        texto - uma checagem textual seria contornavel e rejeitaria caminhos
        validos.
        """
        self._write("b.pdf")
        resolved = resolve_local_path("sub/../b.pdf", root=self.root)
        self.assertEqual(resolved.path, (self.root / "b.pdf").resolve())

    def test_an_absolute_path_outside_the_root_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"fora")
            outside = Path(handle.name)
        self.addCleanup(outside.unlink, missing_ok=True)
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path(str(outside), root=self.root)
        self.assertEqual(caught.exception.code, "PATH_OUTSIDE_ROOT")

    def test_a_symlink_pointing_outside_the_root_is_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"segredo fora da raiz")
            outside = Path(handle.name)
        self.addCleanup(outside.unlink, missing_ok=True)
        link = self.root / "atalho.pdf"
        link.symlink_to(outside)
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("atalho.pdf", root=self.root)
        self.assertEqual(caught.exception.code, "PATH_OUTSIDE_ROOT")

    def test_a_symlink_pointing_inside_the_root_is_accepted(self):
        """A regra proibe escapar da raiz, nao symlink por symlink."""
        target = self._write("real/livro.pdf")
        link = self.root / "atalho.pdf"
        link.symlink_to(target)
        resolved = resolve_local_path("atalho.pdf", root=self.root)
        self.assertEqual(resolved.path, target.resolve())


class FileShapeTests(_RootedCase):
    def test_a_missing_file_is_rejected(self):
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("nao-existe.pdf", root=self.root)
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")

    def test_a_directory_is_rejected(self):
        (self.root / "pasta.pdf").mkdir()
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("pasta.pdf", root=self.root)
        self.assertEqual(caught.exception.code, "NOT_A_REGULAR_FILE")

    def test_an_unsupported_extension_is_rejected(self):
        self._write("planilha.xlsx")
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("planilha.xlsx", root=self.root)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_TYPE")

    def test_a_file_with_no_extension_is_rejected(self):
        self._write("semextensao")
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("semextensao", root=self.root)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_TYPE")

    def test_a_file_over_the_limit_is_rejected(self):
        self._write("grande.pdf", b"x" * 2048)
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("grande.pdf", root=self.root, max_bytes=1024)
        self.assertEqual(caught.exception.code, "FILE_TOO_LARGE")

    def test_a_file_exactly_at_the_limit_is_accepted(self):
        self._write("limite.pdf", b"x" * 1024)
        self.assertEqual(
            resolve_local_path("limite.pdf", root=self.root, max_bytes=1024).size_bytes, 1024
        )

    def test_an_empty_path_is_rejected(self):
        with self.assertRaises(DocumentIngressError) as caught:
            resolve_local_path("   ", root=self.root)
        self.assertEqual(caught.exception.code, "EMPTY_PATH")


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", "KNOWLEDGE_ENGINE_MAX_DOCUMENT_MB")
        }
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_an_unconfigured_root_is_reported_not_guessed(self):
        """Sem raiz configurada o endpoint nao pode aceitar caminho algum.

        Falha FECHADA: nunca cair num default como o cwd, que transformaria a
        maquina inteira em raiz autorizada.
        """
        os.environ.pop("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", None)
        self.assertIsNone(configured_document_root())

    def test_the_configured_root_is_resolved(self):
        with tempfile.TemporaryDirectory() as raw:
            os.environ["KNOWLEDGE_ENGINE_DOCUMENT_ROOT"] = raw
            root = configured_document_root()
        self.assertEqual(root, Path(raw).resolve())

    def test_the_size_limit_has_a_default_and_is_configurable(self):
        os.environ.pop("KNOWLEDGE_ENGINE_MAX_DOCUMENT_MB", None)
        self.assertEqual(configured_max_document_bytes(), 512 * 1024 * 1024)
        os.environ["KNOWLEDGE_ENGINE_MAX_DOCUMENT_MB"] = "7"
        self.assertEqual(configured_max_document_bytes(), 7 * 1024 * 1024)

    def test_a_nonsense_size_limit_falls_back_to_the_default_instead_of_crashing(self):
        os.environ["KNOWLEDGE_ENGINE_MAX_DOCUMENT_MB"] = "muito"
        self.assertEqual(configured_max_document_bytes(), 512 * 1024 * 1024)


class BoundaryTypeTests(unittest.TestCase):
    """O tipo e a fronteira: ``UPLOAD`` tem de caber nele sem mudanca."""

    def test_upload_is_already_a_valid_ingress_value(self):
        resolved = ResolvedDocumentFile(
            path=Path("/tmp/x.pdf"),
            original_filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            ingress="UPLOAD",
        )
        self.assertEqual(resolved.ingress, "UPLOAD")

    def test_the_type_is_immutable(self):
        resolved = ResolvedDocumentFile(
            path=Path("/tmp/x.pdf"),
            original_filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            ingress="LOCAL_PATH",
        )
        with self.assertRaises(Exception):
            resolved.size_bytes = 99  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
