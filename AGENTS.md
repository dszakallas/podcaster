# Agent Instructions for Podcaster

## Environment Management

This repository uses Nix `devenv` for managing its development environment and dependencies.

- **Mandatory Profile**: All commands MUST be executed within a `devenv` shell using the `agents` profile.
  - Command to enter the shell: `devenv shell --profile agents`
- **Shell Detection**: Before running commands, check the `$DEVENV_CMDLINE` environment variable.
  - If it contains `shell`, you are already in the correct environment and can run commands directly.
  - If it is unset or does not contain `shell`, you MUST wrap all commands like so: `devenv shell <command> <args>` (e.g., `devenv shell uv run podcaster ...`).

## Python Tooling

- **Strict `uv` Usage**: This project strictly uses `uv` for all Python package management and script execution.
- **Forbidden Commands**: Do NOT use standalone `pip` or `python` commands. Always use `uv pip`, `uv run`, `uv sync`, etc.

## Workflows and Distribution

The project uses a **preset-based** configuration system for workflows and distribution.

- **Workflow Presets**: Defined under `workflow:` in `podcaster.yaml`. Run them using `uv run podcaster workflow run <preset_name>`.
- **Distribution Presets**: Defined under top-level `rsync:` and `plex:` keys. Use them in workflows via the `distribute:` array or manually via `uv run podcaster dist-plex <id> --preset <name>`.

## Notebook Management

- **Initializing Notebooks**: Notebooks are initialized via `uv run podcaster init-podcast-notebook`.
  - Use `--from-source <path_or_url>` to create a new notebook, upload the first source, and derive/rename the title automatically after successful upload. In case of failure, the remote notebook is cleaned up (deleted).
  - Use `--title <title>` or `--notebook-id <id>` for standard initialization without importing a source.

## Code Quality Checks

After every coding task, and at minimum before each commit, you must pass all three checks:

```bash
prek run --all-files
```

If hooks fail, fix them before committing:

1. **Black** (`uv run black --check src/podcaster`) — reformat with `uv run black src/podcaster`.
2. **Ruff** (`uv run ruff check src/podcaster`) — apply safe fixes with `uv run ruff check src/podcaster --fix`; remaining issues need manual correction.
3. **Pyright** (`uv run pyright src/podcaster`) — fix type errors manually; the wrapper in the shell picks up the correct venv automatically.

Do NOT commit if `prek run --all-files` exits non-zero.

## Hacking around the project

Place support scripts for one-off tasks or experiments in the `hack/` directory.
Files in this directory are ignored from git. These scripts should use the following PEP 723 metadata to depend on the local `podcaster` package:

```python
# /// script
# dependencies = ["podcaster"]
# [tool.uv.sources]
# podcaster = { path = "../", editable = true }
# ///
```

## Pipable Long-Running Task Protocol

For granular control, the project supports a pipable long-running task protocol divided into `create`, `poll`, and `download` steps:

- **Podcast Generation**:
  ```bash
  uv run podcaster podcast create <notebook_id> <type_name> -l <lang> | uv run podcaster podcast poll | uv run podcaster podcast download
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
  uv run podcaster transcription create | uv run podcaster transcription poll | uv run podcaster transcription download
  ```

## Project Reference

Please refer to the project's README for features, configuration details, and architecture:

@./README.md
