"""PHASE 33 - honest visual-dependency disclosure in the exported list.

Focused, DB-free unit tests for ``services/list_export.py``: a synthetic
``GeneratedListDefinition``-shaped dict (the same shape ``dataclasses.asdict``
produces) is fed straight into ``build_render_model`` / ``render_pdf`` /
``render_docx``. No question in this system's answer-key/statement store has
its image embedded (no asset repository exists yet - out of scope), so the
export must never silently print an incomplete statement: when
``has_visual_dependency`` is true it must carry an explicit, honest note.
"""

from __future__ import annotations

import io
import unittest
import zipfile

from agente_ia_edu.services.list_export import (
    _VISUAL_DEPENDENCY_NOTE,
    build_render_model,
    pdf_available,
    render_docx,
    render_pdf,
)


def _item(position, official_number, statement, *, has_visual_dependency):
    return {
        "position": position,
        "question_version_id": f"v-{official_number}",
        "question_id": f"q-{official_number}",
        "source": "ENEM",
        "year": 2024,
        "day": 2,
        "official_number": official_number,
        "booklet_code": "D2_CD5",
        "original_position": official_number,
        "enem_area": "MT",
        "discipline_code": None,
        "content_code": None,
        "subcontent_code": None,
        "classification_state": "UNCLASSIFIED",
        "statement": statement,
        "options": [
            {"key": k, "position": i, "text": f"Alternativa {k}"}
            for i, k in enumerate("ABCDE", start=1)
        ],
        "answer_key": None,
        "has_visual_dependency": has_visual_dependency,
    }


def _definition(items):
    return {
        "configuration": {
            "title": "Lista de teste",
            "instructions": None,
            "activity_mode": "EXERCISE_LIST",
            "answer_key_presentation": "NONE",
            "resolution_style": None,
        },
        "items": items,
        "question_count": len(items),
        "question_version_ids": [it["question_version_id"] for it in items],
        "answer_key_included": False,
        "resolution_included": False,
        "generated_at": "2024-01-01T00:00:00+00:00",
        "selection_fingerprint": "fp",
        "future_compat": {},
    }


class ListExportVisualDependencyTests(unittest.TestCase):
    def setUp(self):
        self.definition = _definition([
            _item(1, 130, "Enunciado com gráfico anexo.", has_visual_dependency=True),
            _item(2, 131, "Enunciado comum, sem dependência visual.",
                  has_visual_dependency=False),
        ])

    def test_render_model_carries_the_flag_per_item(self):
        model = build_render_model(self.definition)
        flags = {it["position"]: it["has_visual_dependency"] for it in model["items"]}
        self.assertTrue(flags[1])
        self.assertFalse(flags[2])

    def test_render_model_defaults_to_false_when_field_is_absent(self):
        # backward compatibility: an old persisted definition without the field
        # (dataclasses.asdict of a pre-PHASE-33 record can't happen going
        # forward, but a defensively-missing key must never crash or claim a
        # dependency that isn't there)
        stripped = _item(1, 999, "Enunciado antigo.", has_visual_dependency=False)
        del stripped["has_visual_dependency"]
        model = build_render_model(_definition([stripped]))
        self.assertFalse(model["items"][0]["has_visual_dependency"])

    @unittest.skipUnless(pdf_available(), "pymupdf not installed")
    def test_pdf_contains_the_note_only_for_the_dependent_question(self):
        import pymupdf as fitz

        model = build_render_model(self.definition)
        pdf_bytes = render_pdf(model)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        self.assertIn(_VISUAL_DEPENDENCY_NOTE, text)
        # exactly one occurrence: only question 1 depends on a visual asset
        self.assertEqual(text.count(_VISUAL_DEPENDENCY_NOTE), 1)

    def test_docx_contains_the_note_only_for_the_dependent_question(self):
        model = build_render_model(self.definition)
        docx_bytes = render_docx(model)
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn(_VISUAL_DEPENDENCY_NOTE, doc_xml)
        self.assertEqual(doc_xml.count(_VISUAL_DEPENDENCY_NOTE), 1)

    def test_no_note_at_all_when_nothing_depends_on_a_visual(self):
        definition = _definition([
            _item(1, 130, "Enunciado comum.", has_visual_dependency=False),
        ])
        model = build_render_model(definition)
        docx_bytes = render_docx(model)
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        self.assertNotIn(_VISUAL_DEPENDENCY_NOTE, doc_xml)
        self.assertNotIn("imagem", doc_xml.lower())


if __name__ == "__main__":
    unittest.main()
