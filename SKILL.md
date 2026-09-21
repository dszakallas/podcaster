---
name: podcast-generation
description: >-
  Automated workflows for creating podcasts with NotebookLM and Gemini: article-driven deep dives and
  topic-driven podcasts (deep dives, debates, author interviews) with web research, AI cover art,
  synchronized transcription, distribution, and Plex synchronization.
---

# Podcast Generation Skill

Use this skill to automate the creation of high-quality, research-enriched podcasts and sync them to a Plex
media library. Two E2E workflow families are available:

- **Article workflow** (`deep_dive_article`): starts from a source URL or file and produces a deep dive
  podcast about that article.
- **Topic workflow** (`topic_workflow`): starts from a topic or prompt, researches the web for sources,
  imports them, and produces one or more podcasts (deep dive, debate, or author interview) about the topic.

## Article Workflow (deep-dive-article)

1. **Identify the Source**: Determine the URL or local file path of the article.
2. **Execute E2E Command**: Run the workflow in the **background** (`is_background: true`) to prevent session
   timeouts.
   - **Command**:
     `podcaster workflow run "${preset_name}" "${source_path_or_url}" --title "${title}"`
   - **Preset Selection**: Use a named preset from `podcaster.yaml` under `workflow.presets` with
     `type: deep_dive_article` (e.g., `deep-dive-article`).
   - **Step Overrides**: `--enrich-web/--no-enrich-web`, `--generate-cover/--no-generate-cover`,
     `--transcribe/--no-transcribe`, `--language/-l` (repeatable), `--length`
     (`short|default|long|auto`), `--workdir/-W`, `--workflow-id/-w`.
3. **Paywall Handling**: Ingestion is handled by composable importers (see `importers:` in `podcaster.yaml`).
   The `default` importer is a chain that falls back from native NotebookLM upload to agent-driven scraping
   (Playwright chrome-devtools) for URLs that fail or match scraper `match` patterns. If a page is paywalled
   (missing text, mid-article truncation, or login/register wall overlays), the scraper agent bails out with a
   structured JSON error payload.
4. **Research Fallback**: Web research enrichment sources that fail to import are retried through the
   configured `enrich_web.spec.fallback_importer` (or `--fallback-importer` on `podcaster research poll`),
   up to `max_import_failures`.

## Topic Workflow (topic-default)

Topic workflows create a notebook from scratch, run web research on the topic, import the discovered sources,
and generate podcasts from the resulting corpus. Use the preset(s) defined under `workflow.presets` with
`type: topic_workflow` (e.g., `topic-default`).

### Prompt-driven (recommended for ad-hoc requests)

Pass a natural language prompt; the recipe (title, research query, podcast types, roles) is inferred
automatically:

```bash
podcaster workflow run topic-default \
  --prompt "Generate a debate about the viability of SaaS startups in the age of AI, do deep research" \
  --title "SaaS in the Age of AI"
```

Recipe inference rules to know when crafting prompts:

- The number of requested podcasts maps 1:1 to generated episodes ("Generate 3 podcasts: ..." produces 3).
- Requests for opposing viewpoints / debates select the `TopicDebate` type; overviews select `TopicDeepDive`.
- Research `mode` defaults to `fast`; explicitly ask for "deep research" / "extensive research" for deep mode.
- Languages and length are only set if explicitly requested; otherwise generator defaults from
  `podcaster.yaml` apply.

### Recipe-driven (explicit control)

For deterministic results, pass a recipe via `--recipe-json` (inline JSON) or `--recipe` (YAML/JSON file)
instead of `--prompt`:

```bash
podcaster workflow run topic-default --recipe-json '{
  "title": "SaaS in the Age of AI",
  "research": {
    "query": "viability of SaaS startups in the age of artificial intelligence, AI commoditizing software",
    "mode": "deep"
  },
  "podcasts": [
    {
      "type": "TopicDebate",
      "focus": "The core disagreement or tension to be debated",
      "roles": [
        "First speaker stance (e.g., venture investor defending SaaS moats)",
        "Opposing speaker stance (e.g., technologist arguing AI commoditizes software)"
      ]
    }
  ]
}'
```

Recipe schema:

- `title` (optional): notebook and series title; overridable with `--title`.
- `research` (required): `query` (web search query) and `mode` (`fast` or `deep`).
- `podcasts` (required, 1+ entries), each with a discriminated `type`:
  - `TopicDeepDive`: overview / educational unpacking of the researched corpus. Optional `roles` (2 speakers).
  - `TopicDebate`: two-host debate between opposing viewpoints. `roles` is required with exactly 2
    contrasting stances. `focus` should state the core disagreement to be debated.
  - `TopicArticle`: interview with the author of a specific article found in the research. Optional `roles`
    (e.g., `["Tech Journalist", "Lead Author"]`), optional `source_id` to pin the article.
- Per-podcast optional fields: `focus`, `agenda`, `languages` (ISO 639-1 codes), `length`
  (`short` | `default` | `long` | `auto`).

### Step toggles

All topic presets accept flags to override the configured step enables:

```bash
podcaster workflow run topic-default -p "..." --no-transcribe --no-generate-cover
```

Flags: `--enrich-web/--no-enrich-web`, `--generate-cover/--no-generate-cover`, `--transcribe/--no-transcribe`.

### Monitoring, status, and recovery

Topic workflows are durable DBOS workflows and long-running (typically 20-40 minutes with deep research,
multiple language tracks, transcription, and distribution). Always run them in the background and monitor:

```bash
podcaster workflow status "${workflow_id}"   # step breakdown of a run
podcaster workflow list                      # recent workflow executions
podcaster workflow resume "${notebook_id}"   # resume a failed/interrupted run
```

Outputs land in the workflow workdir (default `./podcasts/`) as `Title [artifact_id].m4a` files together with
cover art, LRC lyrics, and transcripts.

## Granular Tool Usage & Manual Workflows

For more control or to add content to an existing notebook, use the individual CLI tools. These tools support
**NDJSON piping**, allowing you to build custom processing chains.

### Generating Podcasts for an Existing Notebook

When generating podcasts for an already existing notebook, you MUST use the established NDJSON piping pipeline
to ensure files adhere to project standards. Do not attempt to manually download, rename, or tag artifacts
outside of this pipeline. Using these piped tools automatically guarantees that:

1. Files are correctly named using the `Title [artifact_id].m4a` format.
2. All audio metadata tags (title, album, track, date, artist, album artist, source URL, language) are
   comprehensively filled out.
3. Synchronized LRC files are generated seamlessly.

Generation task types (the `<type_name>` argument of `podcast create`):
`main-article-with-author`, `main-article-language-learning`, `topic-deep-dive`, `topic-debate`.

```bash
podcaster podcast create "${notebook_id}" "main-article-with-author" -l "${lang_code}" \
  | podcaster podcast poll \
  | podcaster podcast download \
  | podcaster tag-podcast --preset "${tag_preset}" --cover "${cover_path}" \
  | podcaster transcription create \
  | podcaster transcription poll \
  | podcaster transcription download
```

Cover generation has an equivalent pipable pipeline:

```bash
podcaster cover create "${notebook_id}" | podcaster cover poll | podcaster cover download
```

### Recovering an Already Generated Artifact

If a podcast was successfully generated in NotebookLM but the local workflow failed to download or tag it, you
can manually inject a task JSON into the pipeline to complete the process. First, list the notebook artifacts
to get the task/artifact ID:

```bash
notebooklm artifact list -n "${notebook_id}" --json
```

Then, inject the task details into the download pipeline via `--arg-json` (a `PodcastGenTask` object; note the
required `"generate-podcast": {"language": "..."}` metadata) and tag it:

```bash
podcaster podcast download --arg-json '{
    "task_id": "YOUR_TASK_ID",
    "notebook_id": "YOUR_NOTEBOOK_ID",
    "status": "completed",
    "title": "The Title",
    "metadata": {"generate-podcast": {"language": "en"}}
  }' | \
  podcaster tag-podcast --preset "${tag_preset}" --cover "./path/to/cover.png"
```

To also regenerate the LRC/transcript, continue the pipe with
`| podcaster transcription create | podcaster transcription poll | podcaster transcription download`.

### Manual Distribution

To distribute the files of a working directory using a named distribution preset (rsync/rclone with optional
attached notifiers):

```bash
podcaster distribute --workdir "${working_dir}" --preset my-media-server [--flag "--dry-run"]
```

If a `metadata.json` exists in the working directory, it is passed to notifiers (Plex rescan, Discord).

### Manual Notebook Initialization

You can initialize a local notebook directory and/or create the remote notebook using:

- **From a source file (recommended)**: Derives the title, creates the remote notebook, uploads the source,
  renames the remote notebook, and creates the local directory. If it fails before successful upload, cleans
  up (deletes) the remote notebook.

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

To manually import a URL and trigger AI web research enrichment:

1. **Import**: `podcaster import-web "${notebook_id}" "${url}"` (or `import-drive` for Google Drive docs,
   `scrape` for agent-driven scraping).
2. **Research**:
   `podcaster research create "${notebook_id}" "${source_id}" --mode deep | podcaster research poll`
   (`poll` accepts `--fallback-importer <preset>` and `--max-import-failures <n>`).

### Listing Local Podcasts

```bash
podcaster list-podcasts --workdir ./podcasts
```

Outputs NDJSON lines with `notebook_id`, `title`, and `local_dir` for locally available podcasts.

## Configuration & Standards

- **Settings**: Primary defaults (languages, length, GCP location) are managed in `podcaster.yaml`. Workflow
  presets are defined under `workflow.presets` (e.g., `deep-dive-article`, `topic-default`) with a `workdir`
  and step configs: `importer` (article workflows), `podcast_generator`, `enrich_web` (`spec.mode`,
  `spec.fallback_importer`, `max_import_failures`), `generate_cover`, `transcribe`, `tagging`
  (`enable`/`spec`), and the `distribute` list. Top-level preset definitions are configured under `agents:`,
  `scrapers:`, `podcast_generators:`, `podcast_transcribers:`, `importers:`, `podcast_tags:`,
  `notifiers:`, and `distributions:`.
- **Infrastructure**: Ensure `PLEX_SERVER_URL` and `PLEX_TOKEN` are in the environment for the Plex notifier.
- **Storage**: Final podcasts are organized under the configured `workflow.workdir` (default `./podcasts/`),
  one directory per workflow run.
