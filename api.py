"""Local HTTP API for analyzing public Python repositories."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analyzer import analyze_repository
from interpretation import (
    InterpretRequest,
    InterpretationGroundingError,
    InterpretationResponse,
    InterpretationUnavailable,
    generate_interpretation,
)
from repository_loader import (
    RepositoryLoadError,
    cleanup_repository,
    load_repository,
    resolve_commit_sha,
    validate_github_url,
)

logger = logging.getLogger(__name__)
analysis_slots = asyncio.Semaphore(2)


class AnalyzeRequest(BaseModel):
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=300)


app = FastAPI(title="Codebase Archaeologist API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$",
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

# Local development only. Do not import or expose these routes in the normal
# API or Oracle validation image unless explicitly enabled at process startup.
if os.getenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED") == "true":
    from local_evidence_api import create_local_evidence_app

    app.mount("/api/evidence", create_local_evidence_app())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    """Clone and analyze one public GitHub repository, then remove the checkout."""
    repository_url = request.repository_url.strip()
    try:
        owner, repository = validate_github_url(repository_url)
    except RepositoryLoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repository_path = None
    async with analysis_slots:
        try:
            repository_path = await asyncio.to_thread(load_repository, repository_url)
            commit_sha = await asyncio.to_thread(resolve_commit_sha, repository_path)
            graph = await asyncio.to_thread(analyze_repository, repository_path)
            # Never expose the server's temporary checkout path to the browser.
            graph.pop("repo_root", None)
            graph["repository"] = {
                "name": f"{owner}/{repository}",
                "url": repository_url,
                "pinned_url": f"https://github.com/{owner}/{repository}/tree/{commit_sha}",
                "source": "github",
            }
            graph["snapshot"] = {"commit_sha": commit_sha}
            graph["source_url"] = repository_url
            return graph
        except RepositoryLoadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Repository analysis failed")
            raise HTTPException(status_code=500, detail="Repository analysis failed.") from exc
        finally:
            if repository_path is not None:
                await asyncio.to_thread(cleanup_repository, repository_path)


@app.post("/api/interpret", response_model=InterpretationResponse)
async def interpret(request: InterpretRequest) -> InterpretationResponse:
    """Interpret one symbol from its bounded evidence packet and source excerpt."""
    try:
        return await asyncio.to_thread(
            generate_interpretation,
            request.evidence_packet,
            request.source_excerpt,
        )
    except InterpretationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InterpretationGroundingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("AI interpretation failed")
        raise HTTPException(status_code=502, detail="AI interpretation failed.") from exc


