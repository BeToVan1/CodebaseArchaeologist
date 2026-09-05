"""Private Linux service boundary; not an unauthenticated public API."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.requests import ClientDisconnect
from deep_quota import CLIENT_KEY, QuotaUnavailable, reserve
from evidence_store import EvidenceSnapshotStore, SnapshotCapacityError, SnapshotUnavailable
from interpretation_evidence import EvidencePreparationError, MAX_SOURCE_FILE_BYTES
from interpretation import EvidencePacket
from workers_ai_client import WorkersAIConfig, WorkersAIError, generate_workers_ai

from repository_loader import RepositoryLoadError, validate_github_url, public_git_environment

JOB_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_MANIFEST_BYTES = 1024 * 1024
WORKER_PATH = Path(__file__).with_name("deep_analysis_worker.py").resolve()


class JobFailure(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class JobResult:
    graph_bytes: bytes
    graph: dict
    source_files: dict[str, bytes]
    commit_sha: str


async def terminate_group(process) -> None:
    # The job owns a new session: killing only the Python PID could orphan Git.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def finish_cleanup(process) -> None:
    """Do not release the job directory/slot until the process is reaped."""
    cleanup = asyncio.create_task(terminate_group(process))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError()


def read_private_evidence(directory: Path, graph: dict) -> tuple[dict[str, bytes], str]:
    root = directory / "evidence"
    manifest_path = root / "manifest.json"
    if root.is_symlink() or not root.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise JobFailure(502, "The analysis service produced invalid private evidence.")
    if manifest_path.stat().st_size > MAX_EVIDENCE_MANIFEST_BYTES:
        raise JobFailure(413, "Evidence snapshot exceeds the hosted limit.")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (ValueError, UnicodeError, OSError) as exc:
        raise JobFailure(502, "The analysis service produced invalid private evidence.") from exc
    commit_sha = manifest.get("commit_sha") if isinstance(manifest, dict) else None
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if commit_sha != graph.get("snapshot", {}).get("commit_sha") or not isinstance(entries, list) or len(entries) > 500:
        raise JobFailure(502, "The analysis service produced invalid private evidence.")
    allowed_paths = {node.get("path") for node in graph.get("nodes", [])}
    sources: dict[str, bytes] = {}
    total = 0
    for index, entry in enumerate(entries):
        expected_name = f"{index:04d}.source"
        if not isinstance(entry, dict) or set(entry) != {"path", "name", "size"}:
            raise JobFailure(502, "The analysis service produced invalid private evidence.")
        path, name, size = entry["path"], entry["name"], entry["size"]
        if (not isinstance(path, str) or path not in allowed_paths or path in sources
                or name != expected_name or type(size) is not int or not 0 <= size <= MAX_SOURCE_FILE_BYTES):
            raise JobFailure(502, "The analysis service produced invalid private evidence.")
        source_path = root / name
        if source_path.is_symlink() or not source_path.is_file() or source_path.stat().st_size != size:
            raise JobFailure(502, "The analysis service produced invalid private evidence.")
        total += size
        if total > MAX_EVIDENCE_BYTES:
            raise JobFailure(413, "Evidence snapshot exceeds the hosted limit.")
        raw = source_path.read_bytes()
        if len(raw) != size:
            raise JobFailure(502, "The analysis service produced invalid private evidence.")
        sources[path] = raw
    return sources, commit_sha


async def run_job(repository_url: str) -> JobResult:
    if not sys.platform.startswith("linux"):
        raise JobFailure(503, "Hosted deep analysis requires the Linux service runtime.")
    with tempfile.TemporaryDirectory(prefix="archaeologist-job-") as directory:
        environment = public_git_environment()
        environment.update(TMPDIR=directory, TEMP=directory, TMP=directory)
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-I", str(WORKER_PATH), cwd=directory, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        ))
        # Cancellation can arrive while the OS is creating the process. Await
        # creation before cleanup so a late-created child is never abandoned.
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            while not spawning.done():
                try:
                    await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    pass
            process = spawning.result()
            await finish_cleanup(process)
            raise
        try:
            await asyncio.wait_for(process.communicate(json.dumps({"repositoryUrl": repository_url}).encode()), JOB_TIMEOUT_SECONDS)
            if process.returncode == 3:
                raise JobFailure(413, "Repository or report exceeds hosted deep-analysis limits.")
            if process.returncode != 0:
                raise JobFailure(502, "Deep analysis failed or reached a resource limit. Check the public repository URL or use the local analyzer.")
            result = Path(directory) / "result.json"
            if result.is_symlink() or not result.is_file() or result.stat().st_size > MAX_OUTPUT_BYTES:
                raise JobFailure(502, "The analysis service produced an invalid report.")
            with result.open("rb") as source:
                data = source.read(MAX_OUTPUT_BYTES + 1)
            if len(data) > MAX_OUTPUT_BYTES:
                raise JobFailure(413, "Report exceeds the hosted output limit.")
            try:
                graph = json.loads(data)
            except (ValueError, UnicodeError) as exc:
                raise JobFailure(502, "The analysis service produced an invalid report.") from exc
            if (not isinstance(graph, dict) or graph.get("schema_version") != "1.1"
                or not isinstance(graph.get("analysis"), dict)
                or graph["analysis"].get("tier") != "deep"
                or not isinstance(graph.get("nodes"), list)
                or not isinstance(graph.get("edges"), list)):
                raise JobFailure(502, "The analysis service produced an invalid report.")
            sources, commit_sha = read_private_evidence(Path(directory), graph)
            return JobResult(data, graph, sources, commit_sha)
        except asyncio.TimeoutError as exc:
            raise JobFailure(504, "Deep analysis exceeded its 60-second time limit.") from exc
        finally:
            # Also reap descendants after normal exit and preserve the slot until
            # cleanup is complete. Cancellation never just abandons a thread.
            await finish_cleanup(process)


def create_app(token: str | None = None, quota_path: str | None = None,
               evidence_store: EvidenceSnapshotStore | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("ARCHAEOLOGIST_SERVICE_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise RuntimeError("Set ARCHAEOLOGIST_SERVICE_TOKEN to a secret of at least 32 non-whitespace ASCII characters.")
    app = FastAPI(title="Private deep analysis service", docs_url=None, redoc_url=None, openapi_url=None)
    quota_path = quota_path if quota_path is not None else os.environ.get("ARCHAEOLOGIST_QUOTA_PATH", "")
    active = False
    interpretation_active = False
    interpretation_enabled = os.environ.get("ARCHAEOLOGIST_INTERPRETATION_ENABLED") == "true"
    workers_ai = WorkersAIConfig.optional(
        os.environ.get("ARCHAEOLOGIST_CF_ACCOUNT_ID", ""),
        os.environ.get("ARCHAEOLOGIST_CF_AI_TOKEN", ""),
    )
    # Match the three-per-hour admission allowance so an owner's third valid
    # analysis is not rejected solely because its first two references remain live.
    store = evidence_store or EvidenceSnapshotStore(max_per_owner=3)

    def authorize(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Service authorization required.", headers={"WWW-Authenticate": "Bearer"})
        keys = request.headers.getlist("x-archaeologist-client-key")
        if len(keys) != 1 or not CLIENT_KEY.fullmatch(keys[0]):
            raise HTTPException(400, "A valid server-derived network key is required.")
        return keys[0]

    async def read_bounded_json(request: Request) -> object:
        async def read_request():
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 2048:
                    raise HTTPException(413, "Request exceeds the 2 KiB limit.")
                body.extend(chunk)
            return json.loads(body)
        try:
            return await asyncio.wait_for(read_request(), 5)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, "Request must be valid JSON.") from exc
        except asyncio.TimeoutError as exc:
            raise HTTPException(408, "Request body timed out.") from exc

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/analyze")
    @app.post("/api/analyze/quota-v1")
    async def analyze(request: Request):
        nonlocal active
        owner_key = authorize(request)
        if active:
            raise HTTPException(429, "The analysis worker is busy. Retry shortly.", headers={"Retry-After": "5"})
        # No await between checking and reserving; no unbounded semaphore queue.
        active = True
        try:
            payload = await read_bounded_json(request)
            url = payload.get("repositoryUrl") if isinstance(payload, dict) else None
            if not isinstance(url, str) or len(url) > 300:
                raise HTTPException(400, "repositoryUrl must be a public GitHub URL.")
            try:
                owner, repo = validate_github_url(url)
            except RepositoryLoadError as exc:
                raise HTTPException(400, "Enter a public GitHub repository URL.") from exc
            try:
                allowed = reserve(quota_path, owner_key)
            except QuotaUnavailable:
                raise HTTPException(503, "Deep-analysis usage storage is unavailable.") from None
            if not allowed:
                raise HTTPException(429, "Deep-analysis allowance reached.",
                                    headers={"Retry-After": "3600", "X-Archaeologist-Limit": "quota"})
            # Poll disconnect while the job runs, rather than letting an abandoned
            # browser request occupy the only slot until the full deadline.
            task = asyncio.create_task(run_job(f"https://github.com/{owner}/{repo}"))
            try:
                while not task.done():
                    if await request.is_disconnected():
                        raise ClientDisconnect()
                    await asyncio.wait({task}, timeout=0.2)
                result = await task
                try:
                    reference = store.register_trusted_snapshot(
                        owner_key=owner_key, graph=result.graph,
                        source_files=result.source_files, commit_sha=result.commit_sha,
                    )
                except SnapshotCapacityError as exc:
                    raise HTTPException(503, "Interpretation evidence capacity is temporarily unavailable.") from exc
                return Response(result.graph_bytes, media_type="application/json", headers={
                    "Cache-Control": "no-store",
                    "X-Archaeologist-Report-Id": reference.report_id,
                    "X-Archaeologist-Report-TTL": str(reference.expires_in_seconds),
                })
            finally:
                if not task.done():
                    task.cancel()
                # Repeated cancellation must not free the slot while the child
                # job is still cleaning up.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()  # consume errors without overriding the response
        except JobFailure as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        except ClientDisconnect:
            return Response(status_code=499)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, "Deep analysis could not complete.") from exc
        finally:
            active = False

    @app.post("/api/evidence/prepare")
    async def prepare_evidence(request: Request):
        owner_key = authorize(request)
        payload = await read_bounded_json(request)
        report_id = payload.get("reportId") if isinstance(payload, dict) else None
        node_id = payload.get("nodeId") if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or set(payload) != {"reportId", "nodeId"}
                or not isinstance(report_id, str) or len(report_id) != 43
                or not isinstance(node_id, str) or not node_id or len(node_id) > 1000):
            raise HTTPException(400, "A valid reportId and nodeId are required.")
        try:
            prepared = store.prepare(owner_key=owner_key, report_id=report_id, node_id=node_id)
        except SnapshotUnavailable as exc:
            raise HTTPException(404, "Analysis evidence is unavailable; run a new analysis.") from exc
        except EvidencePreparationError as exc:
            raise HTTPException(422, "The selected symbol cannot be prepared from trusted evidence.") from exc
        return {
            "commitSha": prepared.commit_sha,
            "evidencePacket": prepared.packet.model_dump(mode="json"),
            "sourceExcerpt": prepared.source_excerpt,
        }

    @app.post("/api/interpret/quota-v1")
    async def interpret(request: Request):
        nonlocal interpretation_active
        owner_key = authorize(request)
        if not interpretation_enabled or workers_ai is None:
            raise HTTPException(503, "AI interpretation is not configured.")
        if interpretation_active:
            raise HTTPException(429, "The interpretation worker is busy. Retry shortly.", headers={"Retry-After": "5"})
        payload = await read_bounded_json(request)
        report_id = payload.get("reportId") if isinstance(payload, dict) else None
        node_id = payload.get("nodeId") if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or set(payload) != {"reportId", "nodeId"}
                or not isinstance(report_id, str) or len(report_id) != 43
                or not isinstance(node_id, str) or not node_id or len(node_id) > 1000):
            raise HTTPException(400, "A valid reportId and nodeId are required.")
        try:
            prepared = store.prepare(owner_key=owner_key, report_id=report_id, node_id=node_id)
        except SnapshotUnavailable as exc:
            raise HTTPException(404, "Analysis evidence is unavailable; run a new analysis.") from exc
        except EvidencePreparationError as exc:
            raise HTTPException(422, "The selected symbol cannot be prepared from trusted evidence.") from exc

        interpretation_active = True
        task = asyncio.create_task(generate_workers_ai(
            EvidencePacket.model_validate(prepared.packet.model_dump(mode="json")),
            prepared.source_excerpt,
            workers_ai,
        ))
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    raise ClientDisconnect()
                await asyncio.wait({task}, timeout=0.2)
            result = await task
            return {
                **result,
                "commitSha": prepared.commit_sha,
                "nodeId": node_id,
            }
        except WorkersAIError as exc:
            print(json.dumps({
                "event": "workers_ai_failed",
                "category": exc.category,
                "providerStatus": exc.provider_status,
                "structuredReason": exc.structured_reason,
            }), file=sys.stderr, flush=True)
            if exc.category == "authentication":
                raise HTTPException(503, "AI provider credentials were rejected.") from exc
            if exc.category == "quota":
                raise HTTPException(429, "The free AI allowance is currently unavailable.") from exc
            if exc.category == "request":
                raise HTTPException(502, "The AI provider rejected the model request.") from exc
            if exc.category == "structured-output":
                raise HTTPException(502, "AI returned an unusable grounded response.") from exc
            raise HTTPException(502, "The AI provider is temporarily unavailable.") from exc
        except ClientDisconnect:
            return Response(status_code=499)
        finally:
            if not task.done():
                task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    break
            if not task.cancelled():
                task.exception()
            interpretation_active = False

    return app
