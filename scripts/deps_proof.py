"""Check that runtime imports are stdlib or local source modules."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"


def imported_top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".", 1)[0])
    return names


def main() -> int:
    if not hasattr(sys, "stdlib_module_names"):
        print("Python 3.10 or newer is required for dependency proof", file=sys.stderr)
        return 2
    local_modules = {path.stem for path in SOURCE.glob("*.py")}
    dependencies: dict[str, set[str]] = {}
    violations = []
    for path in sorted(SOURCE.glob("*.py")):
        names = imported_top_level_names(path)
        dependencies[path.name] = names - local_modules
        violations.extend(
            f"{path.relative_to(ROOT)}: {name}"
            for name in sorted(names - local_modules - sys.stdlib_module_names)
        )
    if violations:
        print("non-stdlib imports found:")
        print("\n".join(violations))
        return 1
    print("stdlib-only dependency proof: PASS")
    for filename, names in dependencies.items():
        rendered = ", ".join(sorted(names)) or "(none)"
        print(f"{filename}: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
