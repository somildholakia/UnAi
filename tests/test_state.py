import pytest

from unai_engine.agent.state import Phase, TaskState


def test_valid_task_progression_and_retry() -> None:
    task = TaskState(task_id="t1", objective="Fix a failing test")
    task.transition(Phase.PLANNING, "Compile the task contract")
    task.transition(Phase.EXPLORING, "Inspect repository evidence")
    task.transition(Phase.EXECUTING, "Try a minimal patch")
    task.transition(Phase.VERIFYING, "Run targeted tests")
    task.transition(Phase.EXPLORING, "Counterexample found; revise hypothesis")
    assert task.phase is Phase.EXPLORING
    assert len(task.events) == 5


def test_invalid_transition_is_rejected() -> None:
    task = TaskState(task_id="t2", objective="Do work")
    with pytest.raises(ValueError, match="Invalid task transition"):
        task.transition(Phase.COMPLETED, "Skip all checks")


def test_terminal_state_cannot_be_reopened_except_blocked_retry() -> None:
    task = TaskState(task_id="t3", objective="Do work")
    task.transition(Phase.BLOCKED, "Missing required information")
    task.transition(Phase.PLANNING, "User supplied the missing constraint")
    assert task.phase is Phase.PLANNING
