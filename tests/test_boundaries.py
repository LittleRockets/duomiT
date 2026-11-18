"""Keep the pure planning modules free from adapters and network execution."""

import ast
from pathlib import Path
import unittest


class BoundaryTests(unittest.TestCase):
    def test_pure_modules_only_import_approved_ports_and_contracts(self):
        root = Path(__file__).resolve().parents[1] / "src" / "duomit"
        approved = {
            "contracts": {"dataclasses", "hashlib", "json", "math", "typing", "errors"},
            "ports": {"typing", "contracts"},
            "planning": {"dataclasses", "contracts", "errors", "ports", "registry"},
            "registry": {"dataclasses", "contracts", "errors"},
            "assistant": {"contracts", "ports"},
        }
        for module, imports in approved.items():
            tree = ast.parse((root / f"{module}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                with self.subTest(module=module, line=getattr(node, "lineno", None)):
                    if isinstance(node, ast.Import):
                        self.assertTrue({item.name for item in node.names} <= imports)
                    if isinstance(node, ast.ImportFrom):
                        self.assertIn(node.module, imports)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"__import__", "eval", "exec", "open"})


if __name__ == "__main__":
    unittest.main()
