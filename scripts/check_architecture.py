#!/usr/bin/env python3
"""Enforce import boundaries that define the agent architecture."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Iterable

AGENT_SUPPORT_MODULES = {"race_utils", "settings"}


def _imported_modules(tree: ast.AST) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                yield node.lineno, f"src.agents.{node.module}"
            elif node.level:
                for alias in node.names:
                    yield node.lineno, f"src.agents.{alias.name}"
            elif node.module:
                yield node.lineno, node.module


def find_boundary_violations(agent_dir: Path) -> list[str]:
    """Return violations without mutating files or depending on import execution."""
    violations: list[str] = []
    for path in sorted(agent_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        current_module = path.stem
        for line, module in _imported_modules(tree):
            if module == "src.react_workflow" or module.startswith(
                "src.react_workflow."
            ):
                violations.append(
                    f"{path}:{line}: agent modules must not depend on the orchestrator"
                )
                continue

            if module == "src.agents":
                violations.append(
                    f"{path}:{line}: role modules must not import the agent package "
                    "aggregator"
                )
                continue

            prefix = "src.agents."
            if not module.startswith(prefix):
                continue
            imported_module = module[len(prefix) :].split(".", 1)[0]
            if imported_module not in AGENT_SUPPORT_MODULES | {current_module}:
                violations.append(
                    f"{path}:{line}: role module '{current_module}' must not import "
                    f"role module '{imported_module}'"
                )

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent-dir",
        type=Path,
        default=Path("src/agents"),
        help="directory containing role-separated agent modules",
    )
    args = parser.parse_args()

    violations = find_boundary_violations(args.agent_dir)
    if violations:
        print("Architecture boundary violations:")
        for violation in violations:
            print(f"- {violation}")
        raise SystemExit(1)

    print("Architecture boundary check passed.")


if __name__ == "__main__":
    main()
