import datetime
import logging
import os
import time
from typing import AsyncGenerator, AsyncIterable, Optional

from mutagen.mp4 import MP4, MP4Cover

from .config import PodcastTagsConfig
from .models import PodcastGenArtifact
from .notebook import get_notebook_url

logger = logging.getLogger(__name__)


def set_mp4_tags(
    path,
    out_path,
    title,
    album,
    track,
    date,
    artists,
    album_artist,
    source,
    cover_path,
    language,
):
    audio = MP4(path)

    if title:
        audio["\xa9nam"] = title
    if album:
        audio["\xa9alb"] = album
    if track:
        audio["trkn"] = [(int(track), 0)]
    if date:
        audio["\xa9day"] = date
    if artists:
        audio["\xa9ART"] = artists
    if album_artist:
        audio["aART"] = album_artist
    if source:
        audio["\xa9cmt"] = source
    if language:
        audio["\xa9lan"] = language

    if cover_path:
        with open(cover_path, "rb") as file_handle:
            cover_data = file_handle.read()
            image_format = MP4Cover.FORMAT_JPEG
            if cover_path.lower().endswith(".png"):
                image_format = MP4Cover.FORMAT_PNG
            audio["covr"] = [MP4Cover(cover_data, imageformat=image_format)]

    audio.save(out_path)


def tag_file(
    audio_file,
    cover,
    title,
    album,
    track,
    date,
    artists,
    album_artist,
    source,
    in_place,
    out,
    language,
):
    if not date:
        date = datetime.datetime.now().strftime("%Y-%m-%d")

    if in_place:
        out_path = audio_file
    elif out:
        out_path = out
    else:
        ext = os.path.splitext(audio_file)[1]
        out_path = f"out{ext}"

    ext = os.path.splitext(audio_file)[1].lower()

    if ext in (".m4a", ".mp4"):
        set_mp4_tags(
            audio_file,
            out_path,
            title,
            album,
            track,
            date,
            artists,
            album_artist,
            source,
            cover,
            language,
        )
    else:
        raise ValueError(
            f"Unsupported file extension: {ext}. Only .m4a and .mp4 are supported."
        )

    return out_path


async def tag_artifacts(
    artifacts: AsyncIterable[PodcastGenArtifact],
    cover_path: Optional[str] = None,
    track_offset: int = 0,
    album: Optional[str] = None,
    created_at: Optional[str] = None,
    *,
    tags_config: PodcastTagsConfig,
) -> AsyncGenerator[PodcastGenArtifact, None]:
    default_album_artist = tags_config.album_artist
    default_artists = tags_config.artists

    auto_track_count = 0

    async for art_item in artifacts:

        notebook_id = art_item.notebook_id
        title = art_item.title
        out_path = art_item.path
        metadata = art_item.metadata.copy()
        gen_podcast_meta = metadata.get("generate-podcast", {})
        language = gen_podcast_meta.get("language")
        source_url = get_notebook_url(notebook_id)

        album_val = album or "NotebookLM Podcast"
        created_at_val = created_at

        auto_track_count += 1
        track_number = track_offset + auto_track_count

        try:
            logger.debug(f"Tagging {out_path} (Track: {track_number})...")

            tag_file(
                audio_file=out_path,
                cover=cover_path,
                title=title,
                album=album_val,
                track=track_number,
                date=created_at_val[:10] if created_at_val else None,
                artists=default_artists,
                album_artist=default_album_artist,
                source=source_url,
                in_place=True,
                out=None,
                language=language,
            )

            metadata["tag-podcast"] = {
                "tagged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "track": track_number,
                "cover": cover_path,
            }

            yield art_item.model_copy(update={"metadata": metadata})

        except Exception as e:
            logger.error(f"Failed to tag {out_path}: {e}")
            yield art_item
