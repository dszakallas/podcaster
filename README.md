# Podcaster

Automation tools for generating podcasts from articles and documents mostly using NotebookLM and `notebooklm-py`.

## Features

- **Automated Workflows**: From a single source file to a Plex-ready podcast in one command.
- **Paywall Bypassing**: Optional fallback for scraping blocked sites using local agents and tools (experimental).
- **Deep Research**: Enriches the notebook with web research related to your source material.
- **AI Cover Art**: Generates custom 1:1 album covers using Gemini.
- **Transcription**: Generates perfectly synchronized LRC lyrics (grouped into 2-second readable segments)
  using Google Cloud Speech-to-Text (Chirp 2).
- **Rsync & Rclone Integration**: Automatically syncs final audio and lyric files to a destination server
  (supporting both `rsync` and `rclone`), tags them, and triggers a library rescan in Plex.
- **Multi-lingual**: Support for generating podcasts in multiple languages.
- **Streaming CLI Pipeline**: Uses asynchronous generators and NDJSON to allow real-time pipelining of tasks
  (`generate | poll | download | tag`).

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

Podcaster reads `podcaster.yaml` from the current directory and validates it with a
strict schema. Unknown keys are rejected. See
[podcaster.example.yaml](podcaster.example.yaml) for the full configuration shape.

Configuration is resolved in this order:

1. Parse and validate `podcaster.yaml`.
2. Resolve `ref` values to their named preset.
3. Fill only unset fields annotated with `env_var: true` from the process environment.

An explicit value in YAML always wins over an environment value. Podcaster does not
load a `.env` file itself.

### Environment-backed configuration

Environment names are derived from the configuration path by uppercasing it and
replacing dots with underscores. The following fields support environment defaults:

| Configuration field | Unscoped environment variable |
| --- | --- |
| `notebooklm.home` | `NOTEBOOKLM_HOME` |
| `notebooklm.storage_state` | `NOTEBOOKLM_STORAGE_STATE` |
| `notebooklm.profile` | `NOTEBOOKLM_PROFILE` |
| `notifiers.<name>.plex.server_library_path` | `PLEX_SERVER_LIBRARY_PATH` |
| `notifiers.<name>.plex.server_url` | `PLEX_SERVER_URL` |
| `notifiers.<name>.plex.token` | `PLEX_TOKEN` |
| `notifiers.<name>.discord.webhook_url` | `DISCORD_WEBHOOK_URL` |
| `notifiers.<name>.discord.bot_token` | `DISCORD_BOT_TOKEN` |
| `notifiers.<name>.discord.channel_id` | `DISCORD_CHANNEL_ID` |

For named notifier presets, Podcaster prepends `NOTIFIERS_<NAME>_` to the
unscoped variable and checks that scoped name first. `<NAME>` is the preset name
uppercased, with non-alphanumeric runs replaced by one underscore. For example,
the `daily news` preset checks
`NOTIFIERS_DAILY_NEWS_DISCORD_WEBHOOK_URL` before
`DISCORD_WEBHOOK_URL`.

Inline notifier configurations have no preset name, so they use only the unscoped
variables. Environment variables with an empty value are treated as unset.

### Environment Variables

`GOOGLE_API_KEY` is consumed directly by the Google Gemini client. It is separate
from Podcaster's YAML configuration and is required when generating covers.

## Usage

### Workflows

Workflows are DBOS-backed, durable podcast pipelines intended for automated, unattended execution. DBOS records each
step, allowing interrupted runs to be recovered rather than restarting completed work. A workflow preset is defined
under `workflow.presets` in `podcaster.yaml` and is run by its preset name.

#### `deep-dive-article`

`deep-dive-article` is the currently available workflow type. It creates a NotebookLM notebook from a local path or
URL, optionally enriches it with web research and cover art, generates audio in one or more languages, tags and
transcribes the output, then distributes it to configured targets.

Its preset accepts these configuration fields:

- `importer`: importer preset or inline importer configuration for the primary source.
- `podcast_generator`: generator preset or inline configuration, including default `languages` and `length`.
- `enrich_web`: `enable`, `retry_count`, and research `spec`.
- `generate_cover`: `enable`, `retry_count`, and cover-generation `spec`.
- `transcribe`: `enable`, `retry_count`, and a `podcast_transcriber` preset or inline configuration.
- `tagging`: `enable` and a podcast-tag preset or inline configuration.
- `distribute`: distribution presets or inline distribution configurations.

For example:

```yaml
workflow:
  workdir: "./podcasts"
  presets:
    deep-dive-article:
      type: deep_dive_article
      importer: { ref: default }
      podcast_generator: { ref: default }
      enrich_web: { enable: true, retry_count: 0, spec: { mode: fast } }
      generate_cover: { enable: true, retry_count: 1, spec: {} }
      transcribe:
        enable: true
        retry_count: 2
        podcast_transcriber: { ref: default }
      tagging: { enable: true, spec: { ref: default } }
      distribute: [{ ref: kolobok }]
```

Run a preset with a required `SOURCE_URL` positional argument. It may be a local path or a URL. Use
`--workflow-id` to supply a stable DBOS workflow ID; otherwise Podcaster creates one.

```bash
# Basic usage with a new article (title is automatically derived)
podcaster workflow run deep-dive-article ./article.pdf --verbose

# Basic usage with a custom title
podcaster workflow run deep-dive-article ./article.pdf --title "My Amazing Podcast" --verbose

# Using a direct URL (supports automated scraping for paywalled sites)
podcaster workflow run deep-dive-article https://example.com/paywalled-site --title "Market Analysis" --verbose

# Overriding preset defaults:
podcaster workflow run deep-dive-article ./article.txt \
  --workflow-id wf_daily_article --title "Quick Podcast" \
  --no-enrich-web --no-generate-cover --no-transcribe
```

The CLI supports `--title`, `--length {short,default,long,auto}`, repeatable `--language`,
`--enrich-web/--no-enrich-web`, `--generate-cover/--no-generate-cover`, `--transcribe/--no-transcribe`,
`--workflow-id`, and `--workdir`.

When a workflow distributes its output, it provides this metadata to the distribution target and its notifiers:

```yaml
id: wf_daily_article
source_url: https://example.com/article
notebook:
  id: notebook-id
  title: Notebook title
  url: https://notebooklm.google.com/notebook/notebook-id
  creation_date: 2026-08-30
preset: deep-dive-article
artifacts:
  - id: artifact-id
    name: Episode title
    language: en
    path: ./podcasts/wf_daily_article/Episode_title.m4a
    lrc_path: ./podcasts/wf_daily_article/Episode_title.lrc
```

Rsync/rclone templates receive this metadata. `notebook.title` and `artifact.name` are sanitized immediately before
template rendering; the metadata itself retains the original values.

The workflow performs these stages:

1. Create a NotebookLM notebook.
2. Upload the source file or URL (optional scraper agent can be used).
3. Perform web research to enrich the context based on `enrich_web` settings.
4. Generate a custom album cover.
5. Trigger podcast generation in your configured default languages.
6. Poll for completion, download the files, and tag them.
7. Transcribe the audio to generate synchronized LRC lyrics (if enabled).
8. Rsync the files to a remote destination (if enabled).
9. Sync the files to your Plex library.

### The Streaming Pipeline

You can run individual steps of the audio generation process using standard Unix pipes.

```bash
export PYTHONUNBUFFERED=1 # Recommended for real-time console output

podcaster podcast create <notebook_id> main-article-with-author | \
podcaster podcast poll | \
podcaster podcast download | \
podcaster tag-podcast --preset default --cover ./album_cover.png | \
podcaster transcription create | \
podcaster transcription poll | \
podcaster transcription download
```

### Standalone Commands

#### Import Web

Imports a URL, Google Drive link, or local file into a notebook using configurable importers (`importers:` section in
`podcaster.yaml`).
Source inputs are normalized to absolute `file://` URIs for local files before matcher evaluation (`normalize_source`).
Importers evaluate regex match patterns (including negative patterns like `!https?://.*wsj\\.com/.*` for paywalled or
unimportable sites).

```bash
# Standard import using default chain importer (native -> scraper)
podcaster import-web <notebook_id> <url>

# Use a specific importer preset
podcaster import-web <notebook_id> <url> --importer scraper
```

#### Create and initialize a local notebook

Creates a new remote notebook or pulls details for an existing one, and initializes its local directory.

> [!NOTE]
> When using `--from-source`, the remote notebook is created first, the source is uploaded, and the title is
> automatically derived from the uploaded source. Only after a successful upload is the local podcast directory created
> (named `[Derived Title] [nlm_<notebook_id>]`). If remote creation or upload fails, the remote notebook is
> automatically cleaned up (deleted) to prevent orphaned notebooks, and no local directory is created.

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
podcaster transcription create \
  --arg-json '{"path": "./podcasts/episode.m4a", "metadata": {"generate-podcast": {"language": "en"}}}' | \
  podcaster transcription poll | \
  podcaster transcription download
```

#### Distribute a podcast

Distributes a podcast using a named distribution preset from the configuration (supporting rsync/rclone with optional
notifiers like Plex or Discord). Supports passing custom flags to `rsync` or `rclone` via `--flag`.

Workflow distribution uses `filename_template` to determine each remote audio and LRC path. Its context includes
`notebook.id`, `notebook.title`, `notebook.creation_date`, `artifact.id`, and `artifact.name`; title and name are
sanitized immediately before rendering. The default preserves the established layout:

```yaml
distributions:
  my-media-server:
    rsync:
      destination: "media:/podcasts"
      filename_template: >-
        {{ notebook.creation_date }} - {{ notebook.title }} [nlm_{{ notebook.id }}]
        /{{ artifact.name }} [{{ artifact.id }}]
```

```bash
podcaster distribute <notebook_id> --preset my-media-server [--flag "--dry-run"]
```

#### Scrape a web target (standalone)

Scrapes the main article text from a target URL using the configured agent.

```bash
podcaster scrape <target_url> --scraper <preset> [--dry-run]
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

## License

MIT
