from __future__ import annotations

import ipaddress
from typing import TypeVar
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from unai_engine.models import (
    PatchApplyResponse,
    PatchProposalRequest,
    PatchProposalResponse,
    ProbeRequest,
    ProbeResponse,
    TaskCompileRequest,
    TaskContract,
    TaskEvidenceSnapshot,
    TaskInspectRequest,
    TaskInspectionResponse,
    TaskProofResponse,
    TaskQualityCheckResponse,
    TaskTestRunResponse,
    WorkspaceFileSnapshot,
)


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class EngineClientError(RuntimeError):
    """A safe client-side representation of Engine request failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _is_loopback_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _segment(value: str) -> str:
    if not value:
        raise ValueError("Path identifiers must not be empty.")
    return quote(value, safe="")


class EngineClient:
    """Typed host-side client for unAI's approval-gated task workflow.

    Keep this client in a trusted local/server process. Never ship its API token
    inside a public browser bundle. Mutating or test-execution methods require an
    explicit user_confirmed=True argument from the host's consent flow.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str = "",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, a query, or a fragment.")
        if parsed.path not in {"", "/"}:
            raise ValueError("base_url must be an origin without a path prefix.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if api_token and len(api_token) < 32:
            raise ValueError("api_token must contain at least 32 characters.")
        if not api_token and not _is_loopback_host(parsed.hostname):
            raise ValueError("A 32+ character api_token is required for non-loopback Engine URLs.")

        self._client = httpx.AsyncClient(
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            headers={
                "Accept": "application/json",
                **({"Authorization": f"Bearer {api_token}"} if api_token else {}),
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "EngineClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[ResponseModel],
        body: BaseModel | dict | None = None,
    ) -> ResponseModel:
        payload = body.model_dump(mode="json", exclude_none=True) if isinstance(body, BaseModel) else body
        try:
            response = await self._client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise EngineClientError("The Engine request timed out.") from exc
        except httpx.RequestError as exc:
            raise EngineClientError("The Engine could not be reached.") from exc

        if not response.is_success:
            try:
                detail = response.json().get("detail", "Engine request failed.")
                if not isinstance(detail, str):
                    detail = "Engine request failed."
            except (ValueError, AttributeError):
                detail = "Engine request failed."
            raise EngineClientError(detail[:1000], response.status_code)

        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise EngineClientError("The Engine returned an invalid response.") from exc

    async def compile_task(self, request: TaskCompileRequest) -> TaskContract:
        return await self._request("POST", "/api/tasks/compile", TaskContract, request)

    async def inspect_task(self, request: TaskInspectRequest) -> TaskInspectionResponse:
        return await self._request("POST", "/api/tasks/inspect", TaskInspectionResponse, request)

    async def read_file(self, task_id: str, file_path: str) -> WorkspaceFileSnapshot:
        path = f"/api/tasks/{_segment(task_id)}/files/{quote(file_path, safe='/')}"
        return await self._request("GET", path, WorkspaceFileSnapshot)

    async def propose_patch(
        self,
        task_id: str,
        request: PatchProposalRequest,
    ) -> PatchProposalResponse:
        path = f"/api/tasks/{_segment(task_id)}/patch-proposals"
        return await self._request("POST", path, PatchProposalResponse, request)

    async def apply_patch(
        self,
        task_id: str,
        proposal_id: str,
        *,
        user_confirmed: bool,
    ) -> PatchApplyResponse:
        if user_confirmed is not True:
            raise ValueError("Explicit user confirmation is required before applying a patch.")
        path = (
            f"/api/tasks/{_segment(task_id)}/patch-proposals/"
            f"{_segment(proposal_id)}/apply"
        )
        return await self._request("POST", path, PatchApplyResponse, {"confirm": "apply-patch"})

    async def run_tests(self, task_id: str, *, user_confirmed: bool) -> TaskTestRunResponse:
        if user_confirmed is not True:
            raise ValueError("Explicit user confirmation is required before running tests.")
        path = f"/api/tasks/{_segment(task_id)}/run-tests"
        return await self._request(
            "POST",
            path,
            TaskTestRunResponse,
            {"confirm": "run-python-tests"},
        )

    async def generate_probes(self, task_id: str, request: ProbeRequest) -> ProbeResponse:
        path = f"/api/tasks/{_segment(task_id)}/probes"
        return await self._request("POST", path, ProbeResponse, request)

    async def refresh_quality(self, task_id: str) -> TaskQualityCheckResponse:
        path = f"/api/tasks/{_segment(task_id)}/quality-check"
        return await self._request("POST", path, TaskQualityCheckResponse)

    async def get_evidence(self, task_id: str) -> TaskEvidenceSnapshot:
        path = f"/api/tasks/{_segment(task_id)}/evidence"
        return await self._request("GET", path, TaskEvidenceSnapshot)

    async def get_proof(self, task_id: str) -> TaskProofResponse:
        path = f"/api/tasks/{_segment(task_id)}/proof"
        return await self._request("GET", path, TaskProofResponse)
