"""Regression test: MaterialStorage's default root must not depend on cwd.

A real production incident: the API process's cwd at request time didn't
match the repo root, so files were written under `<cwd>/var/material_storage`
instead of `<repo>/var/material_storage` -- the endpoint still returned 201
and the storage_uri was saved, but the file was physically unreachable.
"""

from __future__ import annotations

import os
from pathlib import Path

from agente_ia_edu.services.material_storage import MaterialStorage, _PROJECT_ROOT


def test_default_root_is_anchored_to_the_project_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert os.getcwd() == str(tmp_path)

    storage = MaterialStorage()

    assert storage.root == _PROJECT_ROOT / "var" / "material_storage"
    assert storage.root.is_absolute()
    assert not str(storage.root).startswith(str(tmp_path))


def test_project_root_is_the_repo_checkout():
    assert (_PROJECT_ROOT / "pyproject.toml").exists()
