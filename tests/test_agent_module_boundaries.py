from src.agents import (
    AnalyzerAgent,
    ArticleWriterAgent,
    BetBuilderAgent,
    DataCollectorAgent,
    EVCalculatorAgent,
    ReviewerAgent,
    SimulatorAgent,
    WorkflowSettings,
)
from src import react_workflow


def test_reactive_workflow_keeps_legacy_agent_imports() -> None:
    expected_exports = {
        "AnalyzerAgent": AnalyzerAgent,
        "ArticleWriterAgent": ArticleWriterAgent,
        "BetBuilderAgent": BetBuilderAgent,
        "DataCollectorAgent": DataCollectorAgent,
        "EVCalculatorAgent": EVCalculatorAgent,
        "ReviewerAgent": ReviewerAgent,
        "SimulatorAgent": SimulatorAgent,
        "WorkflowSettings": WorkflowSettings,
    }

    for name, implementation in expected_exports.items():
        assert getattr(react_workflow, name) is implementation


def test_agent_implementations_are_owned_by_role_modules() -> None:
    agents = (
        AnalyzerAgent,
        ArticleWriterAgent,
        BetBuilderAgent,
        DataCollectorAgent,
        EVCalculatorAgent,
        ReviewerAgent,
        SimulatorAgent,
    )

    assert all(agent.__module__.startswith("src.agents.") for agent in agents)
    assert WorkflowSettings.__module__ == "src.agents.settings"
