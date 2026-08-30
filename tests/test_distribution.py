"""Regression tests for templated rsync/rclone distribution paths."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from podcaster.distribution.rsync import sync_podcast


def test_sync_podcast_uses_template_for_audio_and_lyrics(tmp_path: Path):
    audio_path = tmp_path / "Episode_title [artifact-id].m4a"
    lrc_path = tmp_path / "Episode_title [artifact-id].lrc"
    audio_path.write_bytes(b"audio")
    lrc_path.write_text("lyrics")
    transferred_paths: list[str] = []

    def capture_rsync(source: str, destination: str, flags: list[str] | None):
        transferred_paths.extend(
            str(path.relative_to(source))
            for path in Path(source).rglob("*")
            if path.is_file()
        )
        assert destination == "remote:podcasts"

    metadata = {
        "notebook": {
            "id": "9c6ab4da-7422-4e9e-b7a5-be904fe3d731",
            "title": "The Billion/Forint Bypass",
            "creation_date": "2026-08-24",
        },
        "artifacts": [
            {
                "id": "artifact-id",
                "name": "Episode title",
                "path": str(audio_path),
                "lrc_path": str(lrc_path),
            }
        ],
    }

    with patch("podcaster.distribution.rsync.rsync_dir", side_effect=capture_rsync):
        asyncio.run(
            sync_podcast(
                working_dir=str(tmp_path),
                destination="remote:podcasts",
                filename_template=(
                    "{{ notebook.creation_date }} - {{ notebook.title }} "
                    "[nlm_{{ notebook.id }}]/{{ artifact.name }} [{{ artifact.id }}]"
                ),
                metadata=metadata,
            )
        )

    assert sorted(transferred_paths) == [
        "2026-08-24 - The_Billion_Forint_Bypass "
        "[nlm_9c6ab4da-7422-4e9e-b7a5-be904fe3d731]/"
        "Episode_title [artifact-id].lrc",
        "2026-08-24 - The_Billion_Forint_Bypass "
        "[nlm_9c6ab4da-7422-4e9e-b7a5-be904fe3d731]/"
        "Episode_title [artifact-id].m4a",
    ]
