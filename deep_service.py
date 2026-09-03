"""Private Linux service boundary; not an unauthenticated public API."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.requests import ClientDisconnect
from deep_quota import CLIENT_KEY, QuotaUnavailable, reserve

from repository_loader import RepositoryLoadError, validate_github_url, public_git_environment

JOB_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
WORKER_PATH = Path(__file__).with_name("deep_analysis_worker.py").resolve()


class JobFailure(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


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


async def run_job(repository_url: str) -> bytes:
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
            return data
        except asyncio.TimeoutError as exc:
            raise JobFailure(504, "Deep analysis exceeded its 60-second time limit.") from exc
        finally:
            # Also reap descendants after normal exit and preserve the slot until
            # cleanup is complete. Cancellation never just abandons a thread.
            await finish_cleanup(process)


def create_app(token: str | None = None, quota_path: str | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("ARCHAEOLOGIST_SERVICE_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise RuntimeError("Set ARCHAEOLOGIST_SERVICE_TOKEN to a secret of at least 32 non-whitespace ASCII characters.")
    app = FastAPI(title="Private deep analysis service", docs_url=None, redoc_url=None, openapi_url=None)
    quota_path = quota_path if quota_path is not None else os.environ.get("ARCHAEOLOGIST_QUOTA_PATH", "")
    active = False

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/analyze")
    @app.post("/api/analyze/quota-v1")
    async def analyze(request: Request):
        nonlocal active
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Service authorization required.", headers={"WWW-Authenticate": "Bearer"})
        if active:
            raise HTTPException(429, "The analysis worker is busy. Retry shortly.", headers={"Retry-After": "5"})
        # No await between checking and reserving; no unbounded semaphore queue.
        active = True
        try:
            async def read_request():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 2048:
                        raise HTTPException(413, "Request exceeds the 2 KiB limit.")
                    body.extend(chunk)
                return json.loads(body)
            try:
                payload = await asyncio.wait_for(read_request(), 5)
            except (ValueError, UnicodeError) as exc:
                raise HTTPException(400, "Request must be valid JSON.") from exc
            except asyncio.TimeoutError as exc:
                raise HTTPException(408, "Request body timed out.") from exc
            url = payload.get("repositoryUrl") if isinstance(payload, dict) else None
            if not isinstance(url, str) or len(url) > 300:
                raise HTTPException(400, "repositoryUrl must be a public GitHub URL.")
            try:
                owner, repo = validate_github_url(url)
            except RepositoryLoadError as exc:
                raise HTTPException(400, "Enter a public GitHub repository URL.") from exc
            keys = request.headers.getlist("x-archaeologist-client-key")
            if len(keys) != 1 or not CLIENT_KEY.fullmatch(keys[0]):
                raise HTTPException(400, "A valid server-derived network key is required.")
            try:
                allowed = reserve(quota_path, keys[0])
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
                return Response(await task, media_type="application/json", headers={"Cache-Control": "no-store"})
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

    return app
