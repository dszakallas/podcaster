# Agent Instructions for Podcaster

## Coding and Architectural Quality

### Prefer libraries and stdlib over custom implementations

Before writing a parser, streaming combinator, or data-structure helper, check whether a maintained
library or the standard library already solves it.

- DO use `aiostream` / `itertools` / `collections` instead of one-off inline generators.
- DO use `pytimeparse2`, `langcodes`, or `pydantic` for their respective domains.
- DO NOT hand-roll JSON duration parsing or language-code mapping unless the existing
  wrapper is provably insufficient.
- DO NOT create a new `utils/` module for a single function that an existing library provides.

### Handling failures and retries

Transient failures (such as network calls or external APIs) must use standardized retry mechanisms rather than
manual sleep loops.

- In workflow code, retries must be handled by DBOS rather than custom retry logic (see
  [Workflows](#workflows)).
- In non-workflow code, DO use `tenacity` for retries; configure `AsyncRetrying` once in shared retry utilities
  (e.g. `utils/retry.py`) where needed.
- DO NOT hand-roll retry loops with manual sleep and exponential backoff.

Example:

```python
# BAD: custom retry loop with manual sleep and backoff
for attempt in range(1, retries + 1):
    try:
        return await fn()
    except Exception as e:
        if attempt == retries:
            raise
        await asyncio.sleep(delay)
        delay *= backoff

# GOOD: tenacity configured in one place
async for attempt in AsyncRetrying(
    stop=stop_after_attempt(retries),
    wait=wait_exponential_jitter(initial=delay, exp_base=backoff),
    retry=retry_if_exception(is_transient),
    reraise=True,
):
    with attempt:
        return await fn()
```

### Static typing and Pydantic

Use static typing throughout the codebase and leverage Pydantic models for structured data, configurations,
and payload validation. Do not bypass the type checker or rely on runtime dynamic checks.

- DO use Pydantic models (`BaseModel`) for domain entities, data transfer objects, configurations, and API payloads.
- DO coerce and validate ambiguous or external responses into Pydantic models (or `TypedDict` when dictionaries
  are required) at the system boundary.
- DO add typed resource wrappers for external interfaces (e.g. `RetryingNotebookLMClient.notebooks: NotebooksAPI`).
- DO use explicit types for collections and metadata (e.g. `list[str]`, `PodcastArtifactMetadata` `TypedDict`)
  instead of `Dict[str, Any]`, `List[Any]`, or bare `Optional`.
- DO NOT introduce `client_any: Any = client` aliases to silence type checkers.
- DO NOT scatter `isinstance(response, dict)` or `getattr(obj, "field", None)` across business logic to defend
  against untyped inputs; type the boundary so callers access typed attributes directly.

Example:

```python
# BAD: defensive getattr and dynamic checks because data or clients are untyped
topic_summary = getattr(guide, "summary", None)
if isinstance(payload, dict):
    title = payload.get("title")

# GOOD: boundary is coerced to Pydantic model or typed wrapper, so callers use attributes directly
topic_summary = guide.summary or source.title
title = payload.title
```

### Restructure files by concept and function

A module should own one idea. When a file exceeds ~400 lines, mixes unrelated concerns, or accumulates helpers
that belong elsewhere, split it.

- DO keep importers, scrapers, workflow plugins, and CLI commands in their own modules.
- DO NOT mix separate concerns in a single file.

### Avoid local edits that create duplication or divergence

Before fixing a single call site, search the codebase for the same pattern. Apply the fix once, in the right
place, and update all call sites.

- DO extract shared helpers when three or more call sites need the same normalization.
- DO update all call sites when changing an interface or adding a typed wrapper.
- DO NOT copy-paste a fix into one caller while leaving the equivalent branch in another module.
- DO NOT rename one copy of a duplicated helper and leave the old alias behind.

### Keep business logic pure and boundary-aware

Configuration loading and raw-input parsing happen at the boundary. Pass concrete, validated models inward. Core
functions should be small, typed, and free of I/O and config-loading concerns.

- DO load configuration objects (`AppConfig`, `NotebookLMConfig`, `GCPConfig`, etc.) in the CLI package
  (`src/podcaster/cli/`) and pass them down to workers and workflows as explicit arguments.
- DO validate and parse JSON strings and CLI inputs at the system boundary before calling internal domain APIs.
- DO require concrete, validated configuration models in domain logic and worker functions; raise an explicit
  `ValueError` at boundary entry points (the CLI package or workflow `run()`) for missing or invalid configs.
- DO keep workflow steps small enough to name clearly.
- DO NOT call `load_config()` inside a utility function, domain logic, or workflow step.
- DO NOT instantiate default configuration objects on the fly in business logic (e.g. `gcp_config or GCPConfig()`).
- DO NOT accept `Union[dict, str, BaseModel]` or multi-type parameter conversions in domain logic.

### Delete before adding

When something is wrong, remove the offending code rather than adding a workaround on top. Dead code, unused
imports, and obsolete aliases must be deleted, not commented out or kept for "just in case."

- DO remove unused imports and dead aliases (`_terminate_process = terminate_process`).
- DO delete helper functions that are no longer called.
- DO trim `__all__` to stop exporting implementation details.
- DO NOT keep deprecated aliases "in case someone is using them" without checking.

### Workflows

Workflows **must use DBOS**. DBOS is a lightweight, database-backed durable execution runtime (backed by SQLite or
PostgreSQL). Its purpose in Podcaster is to record the state and execution progress of workflow steps, providing
fault tolerance and crash resilience. If a long-running unattended workflow fails or is interrupted, DBOS allows
it to be resumed without re-executing already-completed steps (`uv run podcaster workflow resume <workflow_id>`).

**Strict Scoping**: Other code than workflows **must not use DBOS**. Domain logic, importers, scrapers, utility
functions, and CLI commands must remain pure and decoupled from DBOS decorators and lifecycle APIs.

#### DBOS Patterns and Guidelines

For implementing DBOS workflows safely and correctly, consult the `dbos-python` skill installed by the `agents`
profile (`devenv shell --profile agents`). Follow these core DBOS patterns:

- **Workflows vs. Steps**: Orchestration logic belongs in `@DBOS.workflow()`, while external I/O, API calls, and
  compute-heavy operations MUST be isolated in individual `@DBOS.step()` functions.
- **Deterministic Workflows**: Workflow functions must be deterministic so they can be replayed safely
  during recovery. All non-deterministic actions (clock/time lookups, random numbers, network I/O)
  must reside inside steps.
- **Async Method Invocations**: In `async def` code, always use the `_async` variants of DBOS methods (e.g.
  `await DBOS.set_event_async(...)`, `await DBOS.get_result_async(...)`, `await DBOS.start_workflow_async(...)`).
  Calling synchronous DBOS methods in an active event loop raises `RuntimeError`.
- **No Workflow Calls from Steps**: Never call `DBOS.start_workflow` or `DBOS.recv` from inside a step.
- **No Global State**: Do not create or mutate global variables inside workflows or steps; pass state explicitly
  via typed arguments and return values.
- **DBOS 3.x Conventions**: Do not use deprecated 2.x constructs (`@DBOS.transaction`, `@DBOS.scheduled`,
  `Queue(...)`).

## Skill Documentation

`SKILL.md` documents the public interface of this project: CLI commands and options, workflow families and
presets, recipe/configuration schemas, and file layouts.

- **Keep `SKILL.md` in sync**: Whenever a public interface is changed, added, or removed (CLI commands,
  options, or arguments; workflow steps or preset schema; configuration models; pipeline NDJSON schemas;
  output file layouts), update `SKILL.md` in the same change. Do not merge or commit interface changes that
  leave `SKILL.md` stale.

## Developer Environment

- This repository uses Nix `devenv` for managing its development environment and dependencies.

- The devenv shell should provide all required tools. If a new tool is
needed, add it to the `devenv.nix`.

- All commands MUST be executed within a `devenv` shell using the `agents` profile.
  The `agents` profile installs Python skills which should be used.
  - Command to enter the shell: `devenv shell --profile agents`

- Before running commands, check the `$DEVENV_CMDLINE` environment variable.
  - If it contains `shell`, you are already in the correct environment and can run commands directly.
  - If it is unset or does not contain `shell`, you MUST wrap all commands like so:
    `devenv shell -- <command> <args>` (e.g., `devenv shell -- uv run podcaster ...`).

- This project strictly uses `uv` for all Python package management and script execution.
  Do NOT use standalone `pip` or `python` commands; always use `uv pip`, `uv run`, `uv sync`, etc.

## Testing

Unit and integration tests live in `tests/` and cover CLI commands, workflows, importers,
configuration & reference resolution, audio generation, polling, transcription, notifications, and utilities.

Pre-commit hooks run the full test suite on changes to `src/podcaster/` or `tests/`.

To run tests: `uv run pytest tests/ -v` (or `devenv test` via the `devenv:test` task).

When adding new generic, isolated utility functions (pure functions, shared logic, non-trivial
algorithms) or new workflows/importers, add corresponding unit tests in `tests/`. Prefer testing through the public API
rather than private helpers.

## Code Quality Checks

**After editing any file**, run `prek run` to validate all hooks pass.

Do NOT skip pre-commit hooks or commit code that fails checks.

## Hacking around the project

Place support scripts for one-off tasks or experiments in the `hack/` directory.
Files in this directory are ignored from git. These scripts should use the following PEP 723 metadata to depend on the
local `podcaster` package:

```python
# /// script
# dependencies = ["podcaster"]
# [tool.uv.sources]
# podcaster = { path = "../", editable = true }
# ///
```

## Project Reference

Please refer to the project's [README.md](README.md) for features, configuration details, and architecture.
