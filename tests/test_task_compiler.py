from __future__ import annotations

import pytest
from pydantic import ValidationError

from unai_engine.cognition.task_compiler import compile_task
from unai_engine.models import TaskCompileRequest


def test_bugfix_contract_contains_evidence_oriented_steps() -> None:
    contract = compile_task(
        TaskCompileRequest(request="Fix the broken login flow and add a regression test")
    )
    assert contract.task_type == "bugfix"
    assert [step.action for step in contract.steps][:4] == [
        "inspect", "search", "test", "edit"
    ]
    assert any("regression" in check.lower() for check in contract.acceptance_checks)


def test_feature_contract_preserves_constraints_and_flags_stack_unknown() -> None:
    contract = compile_task(
        TaskCompileRequest(
            request="Build a settings page with profile editing and validation",
            constraints=["Do not change the authentication API"],
        )
    )
    assert contract.task_type == "feature"
    assert contract.constraints == ["Do not change the authentication API"]
    assert any("stack is not named" in item for item in contract.unknowns)


def test_approvals_are_not_inferred_from_required_permissions() -> None:
    contract = compile_task(TaskCompileRequest(request="Implement a new profile endpoint"))
    assert contract.permissions == []
    assert set(contract.required_permissions) == {
        "workspace_read", "workspace_write", "run_tests"
    }
    assert set(contract.missing_permissions) == set(contract.required_permissions)


def test_explicit_approvals_are_separate_from_destructive_permission_request() -> None:
    contract = compile_task(
        TaskCompileRequest(
            request="Remove the obsolete generated files",
            permissions=["workspace_read"],
        )
    )
    assert contract.task_type == "feature"
    assert "workspace_write" in contract.required_permissions
    assert "destructive_changes" in contract.required_permissions
    assert "destructive_changes" in contract.missing_permissions
    assert "workspace_read" in contract.permissions


def test_review_contract_does_not_require_write_permission() -> None:
    contract = compile_task(TaskCompileRequest(request="Perform a security review of the API"))
    assert contract.task_type == "review"
    assert "workspace_read" in contract.required_permissions
    assert "workspace_write" not in contract.required_permissions


def test_web_app_does_not_implicitly_request_network_permission() -> None:
    contract = compile_task(TaskCompileRequest(request="Build a React web app"))
    assert contract.task_type == "feature"
    assert "network_access" not in contract.required_permissions


def test_latest_research_requires_explicit_network_approval() -> None:
    contract = compile_task(TaskCompileRequest(request="Research the latest Android runtimes"))
    assert contract.task_type == "research"
    assert "network_access" in contract.required_permissions
    assert "network_access" in contract.missing_permissions


def test_whitespace_request_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskCompileRequest(request="   ")


def test_unknown_permission_is_rejected_at_schema_boundary() -> None:
    with pytest.raises(ValidationError):
        TaskCompileRequest(request="Review this code", permissions=["root_shell"])  # type: ignore[list-item]
