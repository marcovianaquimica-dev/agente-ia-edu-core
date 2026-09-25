"""Coverage for create_app()'s static-portal GET routes (/teacher, /coordination,
/reception, /question-bank, /admin).

These handlers are registered inside `if web_dir.exists():` in
src/agente_ia_edu/api/app.py, but no existing test issues an HTTP request against
them - test_create_app_jwt_wiring.py only calls create_app() itself, and the
per-domain "*_portal_http.py" tests exercise the API routers, not these static
file handlers. Each handler has two branches: serve the portal's own HTML file
when present, or fall back to index.html when it is missing. In this repo the
portal file is always present (teacher.html, coordination.html, question-bank.html,
admin.html all exist under src/agente_ia_edu/web/), so the fallback branch is
exercised by monkeypatching Path.exists for just that one filename.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import agente_ia_edu.api.app as app_module
from agente_ia_edu.api.app import create_app

WEB_DIR = Path(app_module.__file__).parent.parent / "web"


def _patched_exists_returning_false_for(missing_name: str):
    """Return a replacement for Path.exists that lies about one filename only."""
    original_exists = Path.exists

    def fake_exists(self, *args, **kwargs):
        if self.name == missing_name:
            return False
        return original_exists(self, *args, **kwargs)

    return fake_exists


class TestStaticPortalRoutes(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    # --- /teacher: serves teacher.html when present, else index.html ---

    def test_teacher_portal_serves_teacher_html_when_present(self):
        expected = (WEB_DIR / "teacher.html").read_bytes()
        for path in ("/teacher", "/teacher/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)

    def test_teacher_portal_falls_back_to_index_when_teacher_html_missing(self):
        expected = (WEB_DIR / "index.html").read_bytes()
        with mock.patch.object(Path, "exists", _patched_exists_returning_false_for("teacher.html")):
            response = self.client.get("/teacher")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)

    # --- /coordination: serves coordination.html when present, else index.html ---

    def test_coordination_portal_serves_coordination_html_when_present(self):
        expected = (WEB_DIR / "coordination.html").read_bytes()
        for path in ("/coordination", "/coordination/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)

    def test_coordination_portal_falls_back_to_index_when_coordination_html_missing(self):
        expected = (WEB_DIR / "index.html").read_bytes()
        with mock.patch.object(
            Path, "exists", _patched_exists_returning_false_for("coordination.html")
        ):
            response = self.client.get("/coordination")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)

    # --- /reception: always serves reception.html directly (no fallback branch) ---

    def test_reception_portal_serves_reception_html(self):
        expected = (WEB_DIR / "reception.html").read_bytes()
        for path in ("/reception", "/reception/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)

    # --- /question-bank: serves question-bank.html when present, else index.html ---

    def test_question_bank_portal_serves_question_bank_html_when_present(self):
        expected = (WEB_DIR / "question-bank.html").read_bytes()
        for path in ("/question-bank", "/question-bank/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)

    def test_question_bank_portal_falls_back_to_index_when_html_missing(self):
        expected = (WEB_DIR / "index.html").read_bytes()
        with mock.patch.object(
            Path, "exists", _patched_exists_returning_false_for("question-bank.html")
        ):
            response = self.client.get("/question-bank")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)

    # --- /admin: serves admin.html when present, else index.html ---

    def test_admin_portal_serves_admin_html_when_present(self):
        expected = (WEB_DIR / "admin.html").read_bytes()
        for path in ("/admin", "/admin/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, expected)

    def test_admin_portal_falls_back_to_index_when_admin_html_missing(self):
        expected = (WEB_DIR / "index.html").read_bytes()
        with mock.patch.object(Path, "exists", _patched_exists_returning_false_for("admin.html")):
            response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)


if __name__ == "__main__":
    unittest.main()
