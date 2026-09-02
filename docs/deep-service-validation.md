# Private deep-analysis service: validation milestone

This is a separate Linux service, not a change to the public Sites worker. It is
not production-approved until the container tests and live smoke test pass.

## Run from your regular PowerShell terminal

From this repository's root:

```powershell
& .\scripts\Test-DeepService.ps1
```

The script copies 21 explicitly named source/test files into a fresh temporary
build context; it does not enumerate the repository's caches, `.git`, dependencies,
or private files. This avoids Docker traversing inaccessible Windows cache folders.
It builds a disposable validation image, runs the Python regression suite
with networking disabled, then makes one real public GitHub analysis request inside
a second disposable container. It checks authorization, invalid-input handling,
deep symbols and commit pinning. No port is published, host directory mounted,
Docker socket mounted, host token forwarded, or live site updated. A short-lived
service token is generated inside the smoke-test container and never printed.
Use `-SkipLiveGithub` to omit the live request; that is not full rollout validation.
The script removes only its uniquely named test container/image and its verified
temporary build context; shared Docker build caches may remain. It never deletes
project files or changes their permissions. Update the explicit input list when
adding files needed by this Docker image.

If PowerShell blocks script execution, do not disable system policy. Ask your
administrator or run the individual reviewed Docker commands from the script.
Copy the test summary and any failing test/error back to the project task.

## Implemented boundaries

- Dedicated FastAPI entry point: `uvicorn deep_service:create_app --factory`.
- Startup requires `ARCHAEOLOGIST_SERVICE_TOKEN` (at least 32 non-whitespace ASCII
  characters; provision a cryptographically random value). Never use a browser
  `NEXT_PUBLIC_` variable for this secret.
- Only unauthenticated liveness, `/health`; analysis requires a bearer token.
  No interpretation endpoint, API documentation, or CORS is enabled.
- One active request per server process, including request reading and cleanup.
  Additional requests receive 429 instead of forming an unbounded queue.
  Run exactly one Uvicorn worker; scale/capacity must be controlled externally.
- 2 KiB request body, 5-second body deadline, 60-second job wall deadline.
- Each analysis runs under Python isolated mode in its own process group, with a
  temporary working directory and an allowlisted environment without service/LLM
  secrets. Timeout, disconnect and cancellation kill and reap the job group.
- Linux limits: 768 MiB address space, 40 CPU seconds per process, 64 MiB maximum
  single file, 128 file descriptors, and no core dumps. The validation container
  additionally enforces 1 GiB memory, one CPU, 64 PIDs, and a 128 MiB temporary disk.
- Input limits: 500 regular Python files, 1 MiB per Python file, 5 MiB Python source
  total; maximum 10,000 nodes, 30,000 edges, and 10 MiB serialized output. An
  over-limit job fails explicitly; it does not silently claim a complete result.
- Public Git clone disables user/system config, credential helpers, hooks,
  redirects and non-HTTPS protocols. No repository dependencies are installed and
  repository code is parsed, not executed. Symlinked files/directories are skipped.
- Clone size (50 MiB) is checked after cloning; it is **not** a streaming download
  quota. The bounded container filesystem and wall deadline are required safeguards.

## Still required before public use

1. Pass these tests on Linux, including real timeout cleanup and live GitHub access.
2. Choose and approve a Linux hosting provider, budget and regional settings.
3. Keep the service behind an authenticated server-to-server boundary. A shared
   token is not end-user identity, a rate limiter or per-user abuse protection.
4. Add the Sites server proxy and explicit deep/inventory selection without putting
   the service secret in browser code. Test long-running request/cancellation limits.
5. Enforce TLS, ingress limits, provider-level egress policy, per-user quotas,
   bounded concurrency across replicas, logging retention and monitoring.
6. Pin and scan deployment images/dependencies. Current bounded requirements and
   base-image tags are not a reproducible lock or a supply-chain attestation.

Process limits are defense in depth, not a security sandbox for executing hostile
code. Do not mount host secrets, host directories, or the Docker socket into this
service. Resource-limit mechanics follow the [Python resource documentation](https://docs.python.org/3/library/resource.html);
deployment isolation must also follow [Docker security guidance](https://docs.docker.com/engine/security/).
