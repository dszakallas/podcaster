# Podcaster

Automation tools for generating podcasts from articles and documents using NotebookLM and Google Gemini.

## Features

- **Automated Workflows**: From a single source file to a Plex-ready podcast in one command.
- **Deep Research**: Enriches the notebook with web research related to your source material.
- **AI Cover Art**: Generates custom 1:1 album covers using Gemini.
- **Multi-lingual**: Support for generating podcasts in multiple languages.
- **Plex Integration**: Automatically tags generated podcasts and triggers a library rescan in Plex.
- **Streaming CLI Pipeline**: Uses asynchronous generators and NDJSON to allow real-time pipelining of tasks (`generate | poll | download | tag`).

## Prerequisites

- **Python 3.12+** (managed via `uv` or `devenv` recommended)
- **NotebookLM Authentication**: You must have `notebooklm-py` installed and authenticated.
- **Google GenAI API Key**: Required for cover art generation (set as `GOOGLE_API_KEY`).
- **Plex Server**: Required if using the Plex sync feature.

## Installation

This project uses `devenv` for development. To enter the environment:

```bash
devenv shell
```

Alternatively, using `uv`:

```bash
uv sync
uv run podcaster --help
```

## Configuration

The tool reads from `podcaster.yaml` in the current directory.

```yaml
podcast_dir: "podcasts"
podcast_generation:
  languages: ["en", "hu"]
  length: "long"
podcast_tags:
  album_artist: "Your Name"
  artists: ["Your Name"]

workflow:
  deep_dive_single_article:
    enrich_sources: true
    generate_cover: true
    sync_plex: true

plex:
  section_id: 14
  server_library_path: "/mnt/podcasts"
```

### Environment Variables

- `GOOGLE_API_KEY`: Your Google Gemini API key.
- `PLEX_SERVER_URL`: URL to your Plex server (e.g., `http://localhost:32400`).
- `PLEX_TOKEN`: Your Plex authentication token.
- `NOTEBOOKLM_STORAGE_STATE`: Path to your NotebookLM `storage_state.json`.

## Usage

### 1. Full Automated Workflow

The easiest way to create a podcast from a single article:

```bash
podcaster workflow deep-dive-single-article "My Amazing Podcast" ./article.pdf --verbose

# Skip time-consuming steps if not needed:
podcaster workflow deep-dive-single-article "Quick Podcast" ./article.pdf \
  --no-enrich-sources --no-generate-cover --no-sync-plex
```

This workflow will automatically:
1. Create a NotebookLM notebook.
2. Upload the source file.
3. Perform "fast" web research to enrich the context.
4. Generate a custom album cover.
5. Trigger podcast generation in your configured default languages.
6. Poll for completion, download the files, and tag them.
7. Sync the files to your Plex library.

### 2. The Streaming Pipeline

You can run individual steps of the audio generation process using standard Unix pipes. This allows downloads to start immediately as individual language tasks complete, rather than waiting for the entire batch.

```bash
export PYTHONUNBUFFERED=1 # Recommended for real-time console output

podcaster generate-podcast <notebook_id> main-article-with-author | \
podcaster poll-artifact-task | \
podcaster download-podcast | \
podcaster tag-podcast --cover ./album_cover.png
```

### 3. Standalone Commands

#### Create a notebook
Creates the notebook and initializes its local storage directory.
```bash
podcaster init-podcast-notebook "Project Title"
```

#### Research from a source
```bash
podcaster research-from-source <notebook_id> <source_id> --mode deep
```

#### Generate cover art
```bash
podcaster generate-cover <notebook_id>
```

#### Sync to Plex
```bash
podcaster sync-podcast-to-plex <notebook_id> <section_id>
```

> **Note:** The Plex syncer assumes that your local `podcast_dir` is mounted or synced to the Plex library root. The tool does not move files across the network; it simply triggers a library rescan in Plex.

## Architecture

- **`src/podcaster/workflows/`**: High-level orchestrations (e.g., `deep_dive_single_article`).
- **`src/podcaster/audio_gen/core.py`**: Streaming generators for NotebookLM artifact interaction.
- **`src/podcaster/cli.py`**: Command-line interface bridging to the generators.
- **`src/podcaster/research.py`**: Context enrichment logic.
- **`src/podcaster/plex.py`**: Plex library synchronization.
- **`src/podcaster/tagging.py`**: Comprehensive audio metadata (ID3/MP4/OGG) management.
- **`src/podcaster/utils.py`**: Shared folder management and sanitization.

## License

MIT
