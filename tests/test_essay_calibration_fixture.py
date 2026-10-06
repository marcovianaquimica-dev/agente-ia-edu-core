import json
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "essay_calibration_benchmark_v1.json"


def _load():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_fixture_has_thirty_entries():
    entries = _load()
    assert len(entries) == 30


def test_every_entry_has_required_fields():
    for entry in _load():
        for field in (
            "student_ref", "body_text", "expected_scores", "expected_special_situation",
            "normative_status", "normative_divergence", "reference_source", "reference_version",
        ):
            assert field in entry, f"{entry.get('student_ref')} missing {field}"


def test_student_refs_are_unique_and_opaque():
    refs = [entry["student_ref"] for entry in _load()]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("aluno_") for ref in refs)


def test_ten_entries_are_special_situation_unverified():
    entries = _load()
    special = [e for e in entries if e["expected_special_situation"] is not None]
    assert len(special) == 10
    assert all(e["normative_status"] == "UNVERIFIED" for e in special)
    assert all(e["expected_scores"]["total"] == 0 for e in special)


def test_twenty_entries_are_confirmed_non_zero():
    entries = _load()
    confirmed = [e for e in entries if e["normative_status"] == "CONFIRMED"]
    assert len(confirmed) == 20
    assert all(e["expected_special_situation"] is None for e in confirmed)
