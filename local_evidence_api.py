"""Opt-in loopback-only evidence API. Interpretation is disabled by default.

One configured local token represents one local operator, not multiple users.
Do not deploy this authentication adapter on Oracle or Sites.
"""
import asyncio
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from evidence_store import EvidenceSnapshotStore, SnapshotCapacityError, SnapshotUnavailable
from interpretation_evidence import EvidencePreparationError
from interpretation_budget import BudgetExceeded, BudgetUnavailable
from interpretation_execution import ExecutionPolicy, ExecutionUnavailable, generate_budgeted_interpretation
from repository_loader import RepositoryLoadError, cleanup_repository, load_repository, validate_github_url
from snapshot_capture import SnapshotCaptureError, analyze_verified_snapshot

MAX_REQUEST_BYTES = 4096
BODY_TIMEOUT_SECONDS = 5


class LocalEvidenceBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def reject(status, detail):
            headers = {"Cache-Control": "no-store"}
            if status == 401:
                headers["WWW-Authenticate"] = "Bearer"
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)

        token = os.getenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", "")
        if os.getenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED") != "true" or not re.fullmatch(r"[!-~]{32,256}", token):
            return await reject(503, "Local evidence API is disabled or unconfigured.")
        # Trust the transport peer, never forwarded headers. This is a local CLI
        # adapter, not browser sign-in or authorization for a reverse proxy.
        if not scope.get("client") or scope["client"][0] not in {"127.0.0.1", "::1"}:
            return await reject(403, "Local evidence API requires a loopback connection.")
        headers = Request(scope).headers
        if headers.get("origin") is not None:
            return await reject(403, "Browser-origin requests are not enabled for this local API.")
        auth = headers.getlist("authorization")
        expected = ("Bearer " + token).encode("ascii")
        if len(auth) != 1 or not secrets.compare_digest(auth[0].encode("latin-1"), expected):
            return await reject(401, "Local evidence authorization required.")
        if scope["method"] != "POST":
            return await reject(405, "Use POST.")
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json" or headers.get("content-encoding", "identity") != "identity":
            return await reject(415, "Uncompressed JSON is required.")
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        return await reject(413, "Evidence request exceeds the body limit.")
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "Evidence request body timed out.")
        scope.setdefault("state", {})["evidence_owner"] = hashlib.sha256(
            b"local-evidence-owner\0" + token.encode("ascii")
        ).hexdigest()
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def no_store(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, replay, no_store)


class SnapshotAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=300)


class SnapshotSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(alias="reportId", pattern=r"^[A-Za-z0-9_-]{43}$")
    node_id: str = Field(alias="nodeId", min_length=1, max_length=2000)


@dataclass(frozen=True)
class LocalInterpretationRuntime:
    """Server-only injection; the operator owns the SDK client's lifetime.

    No automatic key lookup, budget initialization, pricing, or enablement.
    Never construct this from an HTTP request or publish this local adapter.
    """
    policy: ExecutionPolicy
    ledger: Path
    client: object
    enabled: bool = False


def create_local_evidence_app(*, interpretation_runtime: LocalInterpretationRuntime | None = None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalEvidenceBoundary)
    app.state.evidence_store = EvidenceSnapshotStore()
    app.state.analysis_slot = BoundedSemaphore(1)

    @app.post("/analyze")
    def analyze(payload: SnapshotAnalyzeRequest, request: Request):
        try:
            owner, repository = validate_github_url(payload.repository_url)
        except RepositoryLoadError as exc:
            raise HTTPException(400, "A public GitHub repository URL is required.") from exc
        if not app.state.analysis_slot.acquire(blocking=False):
            raise HTTPException(429, "Local evidence analysis is busy.")
        checkout = None
        try:
            url = f"https://github.com/{owner}/{repository}"
            checkout = load_repository(url)
            graph, sources, commit = analyze_verified_snapshot(checkout)
            graph["repository"] = {"name": f"{owner}/{repository}", "url": url,
                "pinned_url": f"{url}/tree/{commit}", "source": "github"}
            graph["source_url"] = url
            ref = app.state.evidence_store.register_trusted_snapshot(
                owner_key=request.state.evidence_owner, graph=graph, source_files=sources, commit_sha=commit)
            return {"graph": graph, "reportId": ref.report_id, "expiresInSeconds": ref.expires_in_seconds}
        except SnapshotCapacityError as exc:
            raise HTTPException(503, "Evidence storage capacity is unavailable.") from exc
        except (RepositoryLoadError, SnapshotCaptureError) as exc:
            raise HTTPException(422, "Could not capture verified repository evidence.") from exc
        except Exception as exc:
            raise HTTPException(500, "Local evidence analysis failed.") from exc
        finally:
            try:
                if checkout is not None:
                    cleanup_repository(checkout)
            finally:
                app.state.analysis_slot.release()

    @app.post("/prepare")
    def prepare(payload: SnapshotSelectRequest, request: Request):
        try:
            evidence = app.state.evidence_store.prepare(owner_key=request.state.evidence_owner,
                report_id=payload.report_id, node_id=payload.node_id)
        except SnapshotUnavailable as exc:
            raise HTTPException(404, "Analysis snapshot is unavailable; run a new analysis.") from exc
        except EvidencePreparationError as exc:
            raise HTTPException(422, "Selected symbol evidence is unavailable or exceeds limits.") from exc
        return {"commitSha": evidence.commit_sha, "evidencePacket": evidence.packet.model_dump(mode="json"),
                "sourceExcerpt": evidence.source_excerpt, "modelCalled": False}

    @app.post("/interpret")
    def interpret(payload: SnapshotSelectRequest, request: Request):
        runtime = interpretation_runtime
        if not isinstance(runtime, LocalInterpretationRuntime) or runtime.enabled is not True:
            raise HTTPException(503, "Local interpretation is disabled or unconfigured.")
        try:
            result = generate_budgeted_interpretation(store=app.state.evidence_store,
                owner_key=request.state.evidence_owner, report_id=payload.report_id,
                node_id=payload.node_id, policy=runtime.policy, ledger=runtime.ledger,
                client=runtime.client, enabled=runtime.enabled)
            return {"reportId": payload.report_id, "nodeId": payload.node_id,
                    "interpretation": result.model_dump(mode="json")}
        except SnapshotUnavailable as exc:
            raise HTTPException(404, "Analysis snapshot is unavailable; run a new analysis.") from exc
        except EvidencePreparationError as exc:
            raise HTTPException(422, "Selected symbol evidence is unavailable or exceeds limits.") from exc
        except BudgetExceeded as exc:
            raise HTTPException(429, "Interpretation budget is exhausted; no automatic retry.") from exc
        except (BudgetUnavailable, ExecutionUnavailable) as exc:
            raise HTTPException(503, "Interpretation policy, capacity, or storage is unavailable; no automatic retry.") from exc
        except Exception as exc:
            # Provider errors can contain credentials, source, or private paths.
            # Never echo them or retry/refund a possibly accepted provider call.
            raise HTTPException(502, "Interpretation could not be completed safely; no automatic retry.") from exc

    return app
