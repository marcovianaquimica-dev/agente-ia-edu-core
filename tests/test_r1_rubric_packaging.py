"""A built wheel must ship the rubric YAML alongside the loader.

``load_rubric_file`` resolves ``enem_2025.yaml`` via ``Path(__file__).parent``,
so the file has to travel inside the ``agente_ia_edu.rubrics`` package itself.
setuptools only includes non-Python files in a wheel when they are declared as
package data; without that declaration a wheel build silently drops the YAML
and ``load_rubric_file("enem_2025")`` raises ``RubricFileError`` on install,
even though every test here passes (tests read the source tree directly via
``pythonpath = ["src"]``, which masks the gap).
"""

import tomllib
import unittest
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class TestRubricYamlIsPackageData(unittest.TestCase):
    def setUp(self):
        self.config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))

    def test_rubrics_package_declares_yaml_as_package_data(self):
        package_data = self.config.get("tool", {}).get("setuptools", {}).get("package-data", {})
        self.assertIn(
            "agente_ia_edu.rubrics",
            package_data,
            "pyproject.toml must declare package-data for agente_ia_edu.rubrics, "
            "or a built wheel ships loader.py without enem_2025.yaml",
        )
        patterns = package_data["agente_ia_edu.rubrics"]
        self.assertIn(
            "*.yaml",
            patterns,
            "agente_ia_edu.rubrics package-data must include *.yaml so enem_2025.yaml "
            "is packaged into the wheel",
        )
