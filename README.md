# Podcaster

Automation tools for generating podcasts from articles and documents mostly using NotebookLM and `notebooklm-py`.

## Features

- **Automated Workflows**: From a single source file to a Plex-ready podcast in one command.
- **Paywall Bypassing**: Optional fallback for scraping blocked sites using local agents and tools (experimental).
- **Deep Research**: Enriches the notebook with web research related to your source material.
- **AI Cover Art**: Generates custom 1:1 album covers using Gemini.
- **Transcription**: Generates perfectly synchronized LRC lyrics (grouped into 2-second readable segments) using Google Cloud Speech-to-Text (Chirp 2).
- **Rsync & Rclone Integration**: Automatically syncs final audio and lyric files to a destination server (supporting both `rsync` and `rclone`), tags them, and triggers a library rescan in Plex.
- **Multi-lingual**: Support for generating podcasts in multiple languages.
- **Streaming CLI Pipeline**: Uses asynchronous generators and NDJSON to allow real-time pipelining of tasks (`generate | poll | download | tag`).

## Prerequisites

- **Python 3.12+** (managed via `uv` or `devenv` recommended)
- **NotebookLM Authentication**: You must have `notebooklm-py` installed and authenticated.
- **Google GenAI API Key**: Required for cover art generation (set as `GOOGLE_API_KEY`).
- **Plex Server**: Required if using the Plex sync feature.

## Installation & Quick Start

### 1. Using `uvx` / `uv`

Run `podcaster` directly without installing, or install it as a global tool using `uv`:

```bash
# Run podcaster directly
uvx --from git+https://github.com/dszakallas/podcaster.git podcaster --help

# Or install globally as a tool
uv tool install git+https://github.com/dszakallas/podcaster.git
```

For local development with `uv`:

```bash
uv sync
uv run podcaster --help
```

### 2. Using Nix (Flakes)

Drop into an interactive shell with `podcaster` using modern `nix` commands:

```bash
# Run podcaster in a shell directly from GitHub
nix shell github:dszakallas/podcaster

# Or from a local clone
nix shell .#podcaster
```

### 3. Building & Loading Docker Image

Build the bundled Docker image tarball with Nix and load it into your local Docker daemon:

```bash
# Build the x86_64-linux Docker image tarball
nix build .#dockerImages.x86_64-linux.podcaster

# Load the image tarball into Docker
docker load < result
```

### 4. Development Environment with `devenv`

When developing with coding agents, add the `agents` profile, to
enable setting up skills, mcp servers, etc.

This is hidden behind a profile flag for leaner installs on CI.

```bash
devenv shell --profile agents
```

## Configuration

The tool reads from `podcaster.yaml` in the current directory. It uses a strict Pydantic schema for validation.
See [podcaster.example.yaml](podcaster.example.yaml) as an example.

### Environment Variables

- `GOOGLE_API_KEY`: Your Google Gemini API key.
- `PLEX_SERVER_URL`: URL to your Plex server (e.g., `http://localhost:32400`).
- `PLEX_TOKEN`: Your Plex authentication token.
- `NOTEBOOKLM_STORAGE_STATE`: Path to your NotebookLM `storage_state.json`.

## Usage

### 1. Full Automated Workflow

The easiest way to create a podcast using a named preset from `podcaster.yaml`:

```bash
# Basic usage with a new article (title is automatically derived)
podcaster workflow run deep-dive-default ./article.pdf --verbose

# Basic usage with a custom title
podcaster workflow run deep-dive-default ./article.pdf --title "My Amazing Podcast" --verbose

# Using a direct URL (supports automated scraping for paywalled sites)
podcaster workflow run deep-dive-default https://example.com/paywalled-site --title "Market Analysis" --verbose

# Resuming a failed or interrupted workflow run
podcaster workflow resume <notebook_id>

# Overriding preset defaults:
podcaster workflow run deep-dive-default ./article.txt \
  --title "Quick Podcast" --no-enrich-web --no-generate-cover --no-sync-plex
```

This workflow will automatically:

1. Create a NotebookLM notebook.
2. Upload the source file or URL (optional scraper agent can be used).
3. Perform web research to enrich the context based on `enrich_web` settings.
4. Generate a custom album cover.
5. Trigger podcast generation in your configured default languages.
6. Poll for completion, download the files, and tag them.
7. Transcribe the audio to generate synchronized LRC lyrics (if enabled).
8. Rsync the files to a remote destination (if enabled).
9. Sync the files to your Plex library.

### 2. The Streaming Pipeline

You can run individual steps of the audio generation process using standard Unix pipes.

```bash
export PYTHONUNBUFFERED=1 # Recommended for real-time console output

podcaster podcast create <notebook_id> main-article-with-author | \
podcaster podcast poll | \
podcaster podcast download | \
podcaster tag-podcast --cover ./album_cover.png | \
podcaster transcription create | \
podcaster transcription poll | \
podcaster transcription download
```

### 3. Standalone Commands

#### Import Web

Imports a URL, Google Drive link, or local file into a notebook using configurable importers (`importers:` section in `podcaster.yaml`).
Source inputs are normalized to absolute `file://` URIs for local files before matcher evaluation (`normalize_source`). Importers evaluate regex match patterns (including negative patterns like `!https?://.*wsj\\.com/.*` for paywalled or unimportable sites).

```bash
# Standard import using default chain importer (native -> scraper)
podcaster import-web <notebook_id> <url>

# Use a specific importer preset
podcaster import-web <notebook_id> <url> --importer scraper
```

#### Create and initialize a local notebook

Creates a new remote notebook or pulls details for an existing one, and initializes its local directory.

> [!NOTE]
> When using `--from-source`, the remote notebook is created first, the source is uploaded, and the title is automatically derived from the uploaded source. Only after a successful upload is the local podcast directory created (named `[Derived Title] [nlm_<notebook_id>]`). If remote creation or upload fails, the remote notebook is automatically cleaned up (deleted) to prevent orphaned notebooks, and no local directory is created.

```bash
# Initialize a new notebook with a title
podcaster init-podcast-notebook --title "Project Title"

# Initialize an existing notebook locally
podcaster init-podcast-notebook --notebook-id <notebook_id>

# Initialize a new notebook from a source file (title is derived from the source)
podcaster init-podcast-notebook --from-source ./article.txt
```

#### Transcribe a podcast

```bash
podcaster transcription create --arg-json '{"path": "./podcasts/episode.m4a", "metadata": {"generate-podcast": {"language": "en"}}}' | \
podcaster transcription poll | \
podcaster transcription download
```

#### Distribute a podcast

Distributes a podcast using a named distribution preset from the configuration (supporting rsync, rclone, and Plex targets). Supports passing custom flags to `rsync` or `rclone` via `--flag`.

```bash
podcaster distribute <notebook_id> --preset my-media-server [--flag "--dry-run"]
```

#### Scrape a web target (standalone)

Scrapes the main article text from a target URL using the configured agent.

```bash
podcaster scrape <target_url> [--dry-run]
```

#### Edit a workflow state

Opens the workflow's state.json in the editor (using `$EDITOR`) for editing.

```bash
podcaster workflow edit <notebook_id>
```

#### Resume a workflow

Resumes a failed or interrupted workflow run from its state file.

```bash
podcaster workflow resume <notebook_id>
```

## Code Quality

Three tools are enforced on all Python files under `src/podcaster/`:

| Tool | Purpose | Command |
| --- | --- | --- |
| **Black** | Formatting (check-only) | `uv run black --check src/podcaster` |
| **Ruff** | Linting | `uv run ruff check src/podcaster` |
| **Pyright** | Type checking (basic mode) | `uv run pyright src/podcaster` |

All three are wired into pre-commit hooks via `prek`. Run them all at once:

```bash
prek run --all-files
```

Or reformat first, then check:

```bash
uv run black src/podcaster
uv run ruff check src/podcaster --fix
prek run --all-files
```

The hooks are configured in `devenv.nix` and run automatically on `git commit`.

## Architecture

- **`src/podcaster/workflows/`**: High-level orchestrations (e.g., `deep_dive_article`).
- **`src/podcaster/config.py`**: Pydantic schema for `podcaster.yaml`.
- **`src/podcaster/audio_gen/core.py`**: Streaming generators for NotebookLM artifact interaction.
- **`src/podcaster/cli.py`**: Command-line interface bridging to the generators.
- **`src/podcaster/research.py`**: Context enrichment logic and web importing.
- **`src/podcaster/plex.py`**: Plex library synchronization.
- **`src/podcaster/tagging.py`**: Comprehensive audio metadata (ID3/MP4/OGG) management.
- **`src/podcaster/utils.py`**: Shared folder management and sanitization.

## License

MIT
