# Agent Instructions for Podcaster

## Environment Management

This repository uses Nix `devenv` for managing its development environment and dependencies.

- **Mandatory Profile**: All commands MUST be executed within a `devenv` shell using the `agents` profile.
  - Command to enter the shell: `devenv shell --profile agents`
- **Shell Detection**: Before running commands, check the `$DEVENV_CMDLINE` environment variable.
  - If it contains `shell`, you are already in the correct environment and can run commands directly.
  - If it is unset or does not contain `shell`, you MUST wrap all commands like so:
    `devenv shell <command> <args>` (e.g., `devenv shell uv run podcaster ...`).

## Python Tooling

- **Strict `uv` Usage**: This project strictly uses `uv` for all Python package management and script execution.
- **Forbidden Commands**: Do NOT use standalone `pip` or `python` commands. Always use `uv pip`, `uv run`, `uv sync`, etc.
- **Configuration Loading Scope**: Never call `load_config()` outside the CLI package
  (`src/podcaster/cli/`). Configuration objects (such as `gcp_config`, `wf_config`, etc.)
  MUST be loaded in the CLI package and passed down to functions and workflow modules as
  explicit arguments.
- **No On-The-Fly Config Instantiations**: Do NOT instantiate default configuration objects on the fly in
  business logic (e.g. `gcp_config or GCPConfig()`). All configuration parameters are mandatory; domain logic and
  worker functions must require concrete, validated configuration models. Missing or invalid configurations MUST
  raise an explicit `ValueError` at boundary entry points (the CLI package or workflow `run()`).
- **Strict Boundary Parameter Normalization**: Domain logic functions MUST accept a single, well-typed input format.
  Do NOT add representation handling, multi-type parameter conversion, or payload normalization (`Union[dict, str, BaseModel]`)
  inside core business logic or worker functions. All raw parameter parsing (e.g. CLI JSON string deserialization)
  MUST happen at the system boundary in the CLI package before calling internal domain APIs.

## Coding and Architectural Quality

These rules defend against the recurring smells found in this codebase: hand-rolled implementations of solved
problems, defensive dynamic checks that mask type holes, local edits that duplicate or diverge, and modules that
grow past their conceptual boundary.

### Prefer libraries and stdlib over custom implementations

Before writing a parser, retry loop, streaming combinator, or data-structure helper, check whether a maintained
library or the standard library already solves it.

- DO use `tenacity` for retries; configure `AsyncRetrying` once in `utils/retry.py`.
- DO use `aiostream` / `itertools` / `collections` instead of one-off inline generators.
- DO use `pytimeparse2`, `langcodes`, or `pydantic` for their respective domains.
- DO NOT hand-roll exponential backoff, JSON duration parsing, or language-code mapping unless the existing
  wrapper is provably insufficient.
- DO NOT create a new `utils/` module for a single function that an existing library provides.

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

### Type the boundary, do not defend against it with dynamic checks

When an external client or API returns `Any`, add a typed wrapper or coercion at the boundary. Callers should use
typed attributes, not `isinstance(..., dict)` fallbacks or `getattr(..., None)` guards.

- DO add typed resource wrappers for external clients (e.g. `RetryingNotebookLMClient.notebooks: NotebooksAPI`).
- DO model metadata dictionaries with `TypedDict` when the keys are known.
- DO coerce ambiguous responses to Pydantic models or `TypedDict` at the client boundary.
- DO NOT introduce `client_any: Any = client` aliases to silence type checkers.
- DO NOT scatter `isinstance(response, dict)` / `getattr(obj, "field", None)` across business logic.

Example:

```python
# BAD: defensive getattr because the client is untyped
topic_summary = getattr(guide, "summary", None)

# GOOD: client boundary is typed, so callers use attributes directly
topic_summary = guide.summary or source.title
```

### Restructure files by semantic concept

A module should own one idea. When a file exceeds ~400 lines, mixes unrelated concerns, or accumulates helpers
that belong elsewhere, split it. Do not append unrelated code because "it is only a few lines."

- DO split an oversized `cli.py` into `src/podcaster/cli/<domain>.py` modules.
- DO move shared input helpers (`stream_stdin`, `parse_input_stream`) to `cli/input.py`.
- DO keep importers, scrapers, workflow plugins, and CLI commands in their own modules.
- DO NOT add a new command group to a 700-line file.
- DO NOT mix CLI parsing, business logic, and workflow orchestration in one module.

### Avoid local edits that create duplication or divergence

Before fixing a single call site, search the codebase for the same pattern. Apply the fix once, in the right
place, and update all call sites.

- DO search for `client_any`, `isinstance.*dict`, and `getattr.*summary` before choosing a fix.
- DO extract shared helpers when three or more call sites need the same normalization.
- DO update all call sites when changing an interface or adding a typed wrapper.
- DO NOT copy-paste a fix into one caller while leaving the equivalent branch in another module.
- DO NOT rename one copy of a duplicated helper and leave the old alias behind.

### Keep business logic pure and boundary-aware

Configuration loading and raw-input parsing happen at the boundary. Pass concrete, validated models inward. Core
functions should be small, typed, and free of I/O and config-loading concerns.

- DO load `AppConfig` in the CLI package and pass `NotebookLMConfig` / `GCPConfig` down to workers.
- DO validate and parse JSON strings at the CLI boundary.
- DO keep workflow steps small enough to name clearly.
- DO NOT call `load_config()` inside a utility function or workflow step.
- DO NOT accept `Union[dict, str, BaseModel]` in domain logic.

### Use explicit types for collections and metadata

Replace `Dict[str, Any]`, `List[Any]`, and bare `Optional` with specific types. Metadata with known structure
should be a `TypedDict` or Pydantic model, not an open-ended dict.

- DO use `PodcastArtifactMetadata` TypedDict for task and artifact metadata.
- DO annotate public functions like `set_mp4_tags` and `tag_file`.
- DO return `list[str]` instead of `list`.
- DO NOT leave `metadata: Dict[str, Any]` when the keys are stable and known.

### Delete before adding

When something is wrong, remove the offending code rather than adding a workaround on top. Dead code, unused
imports, and obsolete aliases must be deleted, not commented out or kept for "just in case."

- DO remove unused imports and dead aliases (`_terminate_process = terminate_process`).
- DO delete helper functions that are no longer called.
- DO trim `__all__` to stop exporting implementation details.
- DO NOT keep deprecated aliases "in case someone is using them" without checking.

## Workflows and Distribution

The project uses a **preset-based** configuration system for workflows, distribution, importers, and ID3 tagging.

- **Workflow Presets**: Defined under `workflow:` in `podcaster.yaml`. Run them using
  `uv run podcaster workflow run <preset_name>`. Resume them using `uv run podcaster workflow resume <notebook_id>`.
  Workflow steps include `importer`, `podcast_generator`, `enrich_web`, `generate_cover`, `transcribe`, `tagging`
  (with `enable: bool` and `spec:` holding a `ref` or inline tags configuration), and `distribute`.
- **Distribution Presets**: Defined under top-level `distributions:` key (containing `rsync` distribution
  configurations and optional attached `notifiers`). `rsync` distributions support `method` (`rsync` or `rclone`),
  `destination`, `flags`, and `notifiers`. Use distribution presets in workflows via the `distribute:` array or
  manually via `uv run podcaster distribute <id> [--preset <name>] [--flag ...]`.
- **Notifier Presets**: Defined under top-level `notifiers:` key (containing `plex` or `discord` notifier
  configurations). Notifiers are executed concurrently after distribution operations complete.
- **ID3 Tagging Presets**: Defined under top-level `podcast_tags:` key as a preset dictionary mapping preset names
  (e.g. `default`) to `album_artist` and `artists`.

## Notebook Management

- **Initializing Notebooks**: Notebooks are initialized via `uv run podcaster init-podcast-notebook`.
  - Use `--from-source <path_or_url>` to create a new notebook, upload the first source, and derive/rename the
    title automatically after successful upload. In case of failure, the remote notebook is cleaned up (deleted).
  - Use `--title <title>` or `--notebook-id <id>` for standard initialization without importing a source.

## Article Importing & Web Scraping

- **Importers Architecture**: Ingestion is managed via composable importers implementing the `Importer` interface
  (`NativeImporter`, `ScraperImporter`, `ChainImporter`):
  - `native`: Direct NotebookLM upload for URLs, Google Drive links, and local files.
  - `scraper`: Agent-driven web scraper configured via top-level `scrapers:` section.
  - `chain`: Composite importer executing a list of sub-importers (`importers`) in priority order with fallback on
    failure.
- **Source Normalization**: Input sources (local paths, `file://` URLs, web URLs, Drive links) are normalized into
  absolute `file://` URIs before match evaluation (`normalize_source`).
- **Match Expressions**: Importers use regex match rules (including negative patterns starting with `!` like
  `!https?://.*wsj\\.com/.*`) to determine applicability. Importers without a `match` block implicitly default to
  `[".*"]`.
- **Paywall Detection**: Scraper agent prompts detect paywalls (missing text, mid-article truncation, or
  login/register overlays) and bail out with a structured JSON error payload.
- **Research Enrichment Fallback**: Web research jobs use `fallback_importer` (an `ImporterRef` or inline
  `ImporterConfig`) and `max_import_failures` (threshold of allowed failed imports before failing research).

## Developer Environment

The devenv shell provides all required tools (Python 3.12, uv, pyright, ruff, black, prek).
Dev dependencies (pytest) are installed automatically via `uv sync` using the `dev` dependency group
defined in `pyproject.toml`.

- **Run tests**: `uv run pytest tests/ -v` (or `devenv test` via the `devenv:test` task).
- **Install new dev dependencies**: Add them under `[dependency-groups] dev = [...]` in `pyproject.toml`,
  then run `uv sync`.

## Testing

Unit tests live in `tests/` and are organized by module:

- `tests/test_ref_resolver.py` — `RefResolver`, `Ref` (resolution, cycles, caching, coercion, extra fields)
- `tests/test_utils.py` — `sanitize`, `get_notebook_dir_name`, `find_notebook_dir`, duration parsing
- `tests/test_research_utils.py` — `evaluate_importer_match`, `extract_drive_file_id`, `normalize_source`
- `tests/test_audio_gen_utils.py` — `duration_to_audio_length`

Pre-commit hooks run the full test suite on changes to `src/podcaster/` or `tests/`.

When adding new generic, isolated utility functions (pure functions, shared logic, non-trivial
algorithms), add corresponding unit tests in `tests/`. Prefer testing through the public API
rather than private helpers.

## Code Quality Checks

**After editing any file**, run `prek run --all-files` to validate all hooks pass. Do this
after every coding task and at minimum before each commit. This is the single source of truth
for code quality — it runs all hooks (black, ruff, pyright, pytest, markdownlint, nixfmt).

```bash
prek run --all-files
```

If hooks fail, fix them before proceeding:

1. **Black** — reformat with `uv run black src/podcaster`.
2. **Ruff** — apply safe fixes with `uv run ruff check src/podcaster tests/ --fix`;
   remaining issues need manual correction.
3. **Pyright** — fix type errors manually.
4. **Pytest** — fix failing tests before committing.
5. **Markdownlint** — fix markdown formatting issues in documentation files.
6. **Nixfmt** — reformat Nix files with `nixfmt`.

Do NOT commit if `prek run --all-files` exits non-zero.

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

## Pipable Long-Running Task Protocol

For granular control, the project supports a pipable long-running task protocol divided into `create`, `poll`, and
`download` steps:

- **Podcast Generation**:

  ```bash
  uv run podcaster podcast create <notebook_id> <type_name> -l <lang> | \
    uv run podcaster podcast poll | \
    uv run podcaster podcast download
  ```

- **Cover Generation**:

  ```bash
  uv run podcaster cover create <notebook_id> | uv run podcaster cover poll | uv run podcaster cover download
  ```

- **Web Research / Enrichment**:

  ```bash
  uv run podcaster research create <notebook_id> <source_id> | uv run podcaster research poll
  ```

- **Transcription**:

  ```bash
  uv run podcaster transcription create | \
    uv run podcaster transcription poll | \
    uv run podcaster transcription download
  ```

## Project Reference

Please refer to the project's README for features, configuration details, and architecture:

@./README.md
