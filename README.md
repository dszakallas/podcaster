# Podcaster

Automation tools for generating podcasts from articles and documents using NotebookLM and Google Gemini.

## Features

- **Automated Workflow**: From a single source file to a Plex-ready podcast in one command.
- **Deep Research**: Enriches the notebook with web research related to your source material.
- **AI Cover Art**: Generates custom 1:1 album covers using Gemini.
- **Multi-lingual**: Support for generating podcasts in multiple languages.
- **Plex Integration**: Automatically tags generated podcasts and triggers a library rescan in Plex.
- **Modular CLI**: Each step of the process is available as a standalone command, supporting JSON-piping for advanced automation.

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
generate:
  languages: ["en"]
  length: "long"
tags:
  album_artist: "Your Name"
  artists: ["Your Name"]
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

The easiest way to create a podcast:

```bash
podcaster create-podcast "My Amazing Podcast" --source-file ./article.pdf
```

This will:
1. Create a NotebookLM notebook.
2. Upload the source file.
3. Perform "fast" web research to enrich the context.
4. Generate a custom album cover.
5. Trigger podcast generation in the specified languages.
6. Poll for completion and download the files.
7. Tag the files and sync them to your Plex library.

### 2. Standalone Commands

You can also run individual steps:

#### Create a notebook
```bash
podcaster init-podcast-notebook "Project Title"
```

#### Research from a source
```bash
podcaster research-from-source <notebook_id> <source_id> --mode deep
```

#### Generate cover art
```bash
podcaster gen-cover <notebook_id>
```

#### Generate and download (Step-by-step)
```bash
# Start generation
podcaster generate-podcast <notebook_id> main_article_with_author -l en --length long > tasks.json

# Poll for completion
podcaster poll-artifact-task < tasks.json > completed.json

# Download and tag
podcaster download-podcast --cover ./cover.png < completed.json
```

#### Sync to Plex
```bash
podcaster sync-podcast-to-plex <notebook_id> <section_id>
```

> **Note:** The Plex syncer assumes that your `podcast_dir` is already the Plex library root. The tool does not move or "sync" files to a different location; it simply triggers a library rescan in Plex after downloading the files to the local directory.

## Architecture

- **`src/podcaster/workflow.py`**: High-level orchestration.
- **`src/podcaster/audio_gen/core.py`**: Interaction with NotebookLM's artifacts and local file management.
- **`src/podcaster/research.py`**: Context enrichment logic.
- **`src/podcaster/plex.py`**: Plex library synchronization.
- **`src/podcaster/tagging.py`**: MP3 metadata (ID3) management.

## License

MIT
