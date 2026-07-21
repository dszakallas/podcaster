---
name: podcast-generation
description: Automated workflow for creating podcasts from articles using NotebookLM and Gemini, including web research, AI cover art, synchronized transcription, rsync deployment, and Plex synchronization.
---

# Podcast Generation Skill

Use this skill to automate the creation of high-quality, research-enriched podcasts from web articles or documents and sync them to a Plex media library.

## Primary Workflow (E2E Pipeline)

The most efficient way to generate a podcast is using the **End-to-End (E2E) pipeline**, which automates notebook creation, source ingestion (with paywall bypassing), AI research, cover generation, and Plex synchronization in a single step.

1. **Identify the Source**: Determine the URL or local file path of the article.
2. **Execute E2E Command**: Run the workflow in the **background** (`is_background: true`) to prevent session timeouts.
   - **Command**: `podcaster workflow run "${preset_name}" "${source_path_or_url}" --title "${title}"` (The `--title` parameter is optional; if not set, the title is derived from the notebook/source.)
   - **Preset Selection**: Use a named preset from `podcaster.yaml` (e.g., `default`, `deep-dive-default`).
   - **Bypassing Blocks & Paywalls**: Ingestion automatically handles paywalled or unimportable sites (matching `unimportables` regexes) via agent-based scraping using Playwright MCP. If an article is paywalled (missing text, mid-article truncation, or login/register wall overlays), the agent bails out returning a structured JSON error.
   - **Importers**: Ingestion behavior can be set to `default`, `scrape` (forced agent scraping), or `ignore`. Failed research enrichment sources trigger the configured `fallback_mechanism` (`scrape` or `ignore`).

## Granular Tool Usage & Manual Workflows

For more control or to add content to existing notebooks, use the individual CLI tools. These tools support **NDJSON piping**, allowing you to build custom processing chains.

### Generating Podcasts for an Existing Notebook

When generating podcasts for an already existing notebook, you MUST use the established NDJSON piping pipeline to ensure files adhere to project standards. Do not attempt to manually download, rename, or tag artifacts outside of this pipeline. Using these piped tools automatically guarantees that:

1. Files are correctly named using the `Title [artifact_id].m4a` format.
2. All audio metadata tags (title, album, track, date, artist, album artist, source URL, language) are comprehensively filled out.
3. Synchronized LRC files are generated seamlessly.

```bash
podcaster podcast create "${notebook_id}" "main-article-with-author" -l "${lang_code}" \
  | podcaster podcast poll \
  | podcaster podcast download \
  | podcaster tag-podcast --cover "${cover_path}" \
  | podcaster transcription create \
  | podcaster transcription poll \
  | podcaster transcription download
```

### Recovering an Already Generated Artifact

If a podcast was successfully generated in NotebookLM but the local workflow failed to download or tag it, you can manually inject a JSON schema into the pipeline to complete the process. First, list artifacts to get the `artifact_id`:

```bash
notebooklm artifact list -n "${notebook_id}" --json
```

Then, inject the artifact details into the download pipeline via `--arg-json`. Note the required `"generate-podcast": {"notebook_id": "..."}` metadata schema:

```bash
podcaster podcast download --arg-json '{"artifact_id": "YOUR_ARTIFACT_ID", "notebook_id": "YOUR_NOTEBOOK_ID", "type_id": "audio", "title": "The Title", "status": "completed", "generate-podcast": {"notebook_id": "YOUR_NOTEBOOK_ID"}}' | podcaster tag-podcast --cover "./path/to/cover.png"
```

### Manual Distribution

To distribute a podcast using a named distribution preset (rsync, rclone, or Plex target):

```bash
podcaster distribute "${notebook_id}" --preset my-media-server [--flag "--dry-run"]
```

### Manual Notebook Initialization

You can initialize a local notebook directory and/or create the remote notebook using:

- **From a source file (recommended)**: Derives the title, creates the remote notebook, uploads the source, renames the remote notebook, and creates the local directory. If it fails before successful upload, cleans up (deletes) the remote notebook.

  ```bash
  podcaster init-podcast-notebook --from-source ./article.txt
  ```

- **With a specific title**:

  ```bash
  podcaster init-podcast-notebook --title "My Title"
  ```

- **Using an existing remote notebook**:

  ```bash
  podcaster init-podcast-notebook --notebook-id <notebook_id>
  ```

### Manual Web Import & Research

To manually import a URL and trigger an AI research deep-dive:

1. **Import**: `podcaster import-web "${notebook_id}" "${url}"`
2. **Research**: `podcaster research create "${notebook_id}" "${source_id}" --mode deep | podcaster research poll`

## Configuration & Standards

- **Settings**: Primary defaults (languages, length, GCP location, unimportable regexes) are managed in `podcaster.yaml`. Workflow options use a nested schema (e.g., `workflow.deep_dive_article.generate_cover.enable`, `workflow.deep_dive_article.transcribe.enable`, `workflow.deep_dive_article.tagging.enable`, `workflow.deep_dive_article.distribute`). Top-level preset definitions are configured under `scrapers:`, `podcast_generators:`, `podcast_transcribers:`, `importers:`, `podcast_tags:`, and `distributions:`.
- **Infrastructure**: Ensure `PLEX_SERVER_URL` and `PLEX_TOKEN` are in the environment for sync tasks.
- **Storage**: Temporary files are stored in `.tmp/`, and final podcasts are organized by notebook title in the configured `podcast_dir`.
