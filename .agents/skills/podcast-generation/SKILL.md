# Podcast Generation Skill

Use this skill when the user wants to create a podcast from a web article or a search query and sync it to Plex.

## Workflow

Follow these steps precisely:

1. **Extract Article Content**
   - Use `mcp_playwright` tools to navigate to the link (or search for it first).
   - Extract the article and capture: Content, Creation Date, Author, Site Name, Category, and Original URL.
   - Format this metadata clearly at the top of the extracted text.

2. **Save Source File**
   - Save the formatted extracted text to a local file, e.g., `article.txt`.

3. **Run Podcast Generation Workflow**
   - Execute the fully automated workflow in the background. Note that this command takes ~20 minutes, so it is highly recommended to invoke a subagent using the `invoke_agent` tool (e.g. using the `generalist` subagent) to execute this command so it doesn't block the main conversation.
   - Run: `uv run podcaster create-podcast "${article_title}" --source-file article.txt`
   - The workflow will automatically handle notebook initialization, source uploading, research enrichment, cover generation, podcast generation, downloading, tagging, and syncing to Plex.

## Configuration
- Ensure `PLEX_SERVER_URL` and `PLEX_TOKEN` are available in the environment.
- The default podcast directory and generation settings are managed via `podcaster.yaml`.
