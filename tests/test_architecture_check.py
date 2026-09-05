from pathlib import Path

from scripts.check_architecture import find_boundary_violations


def _write_module(directory: Path, name: str, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(source, encoding="utf-8")


def test_allows_shared_agent_support_modules(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    _write_module(
        agent_dir,
        "analyzer.py",
        "from src.agents.settings import WorkflowSettings\n"
        "from src.agents.race_utils import race_order\n",
    )

    assert find_boundary_violations(agent_dir) == []


def test_rejects_role_to_role_import(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    _write_module(
        agent_dir,
        "analyzer.py",
        "from src.agents.reviewer import ReviewerAgent\n",
    )

    violations = find_boundary_violations(agent_dir)

    assert len(violations) == 1
    assert "must not import role module 'reviewer'" in violations[0]


def test_rejects_relative_role_to_role_import(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    _write_module(agent_dir, "analyzer.py", "from .reviewer import ReviewerAgent\n")

    violations = find_boundary_violations(agent_dir)

    assert len(violations) == 1
    assert "must not import role module 'reviewer'" in violations[0]


def test_rejects_agent_to_orchestrator_import(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents"
    _write_module(
        agent_dir,
        "analyzer.py",
        "from src.react_workflow import ReactiveRaceWorkflow\n",
    )

    violations = find_boundary_violations(agent_dir)

    assert len(violations) == 1
    assert "must not depend on the orchestrator" in violations[0]
