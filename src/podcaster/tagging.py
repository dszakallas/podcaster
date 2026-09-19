import datetime
import logging
import os
import time
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field

from mutagen.mp4 import MP4, MP4Cover

from .config import PodcastTagsConfig
from .models import PodcastGenArtifact
from .notebook import get_notebook_url

logger = logging.getLogger(__name__)


@dataclass
class AudioTags:
    """Metadata tags for an audio file."""

    title: str | None = None
    album: str | None = None
    track: int = 1
    total_tracks: int | None = None
    date: str | None = None
    artists: list[str] = field(default_factory=list)
    album_artist: str | None = None
    source: str | None = None
    cover_path: str | None = None
    language: str | None = None


def set_mp4_tags(
    path: str,
    out_path: str,
    tags: AudioTags,
) -> None:
    audio = MP4(path)

    if tags.title:
        audio["\xa9nam"] = tags.title
    if tags.album:
        audio["\xa9alb"] = tags.album
    if tags.track:
        audio["trkn"] = [(int(tags.track), int(tags.total_tracks or 0))]
    if tags.date:
        audio["\xa9day"] = tags.date
    if tags.artists:
        audio["\xa9ART"] = tags.artists
    if tags.album_artist:
        audio["aART"] = tags.album_artist
    if tags.source:
        audio["\xa9cmt"] = tags.source
    if tags.language:
        audio["\xa9lan"] = tags.language

    if tags.cover_path:
        with open(tags.cover_path, "rb") as file_handle:
            cover_data = file_handle.read()
            image_format = MP4Cover.FORMAT_JPEG
            if tags.cover_path.lower().endswith(".png"):
                image_format = MP4Cover.FORMAT_PNG
            audio["covr"] = [MP4Cover(cover_data, imageformat=image_format)]

    audio.save(out_path)


def tag_file(
    audio_file: str,
    tags: AudioTags,
    in_place: bool = True,
    out: str | None = None,
) -> str:
    if not tags.date:
        tags.date = datetime.datetime.now().strftime("%Y-%m-%d")

    if in_place:
        out_path = audio_file
    elif out:
        out_path = out
    else:
        ext = os.path.splitext(audio_file)[1]
        out_path = f"out{ext}"

    ext = os.path.splitext(audio_file)[1].lower()

    if ext in (".m4a", ".mp4"):
        set_mp4_tags(audio_file, out_path, tags)
    else:
        raise ValueError(
            f"Unsupported file extension: {ext}. Only .m4a and .mp4 are supported."
        )

    return out_path


async def tag_artifacts(
    artifacts: AsyncIterable[PodcastGenArtifact],
    cover_path: str | None = None,
    track_offset: int = 0,
    album: str | None = None,
    created_at: str | None = None,
    *,
    tags_config: PodcastTagsConfig,
    total_tracks: int | None = None,
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
        explicit_track = metadata.get("track")
        if explicit_track is not None:
            track_number = int(explicit_track)
        else:
            track_number = track_offset + auto_track_count

        total_tracks_val = total_tracks or metadata.get("total_tracks")

        try:
            logger.debug(f"Tagging {out_path} (Track: {track_number})...")

            tags = AudioTags(
                title=title,
                album=album_val,
                track=track_number,
                total_tracks=total_tracks_val,
                date=created_at_val[:10] if created_at_val else None,
                artists=default_artists,
                album_artist=default_album_artist,
                source=source_url,
                cover_path=cover_path,
                language=language,
            )
            tag_file(audio_file=out_path, tags=tags, in_place=True)

            metadata["tag-podcast"] = {
                "tagged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "track": track_number,
                "total_tracks": total_tracks_val,
                "cover": cover_path,
            }

            yield art_item.model_copy(update={"metadata": metadata})

        except Exception as e:
            logger.error(f"Failed to tag {out_path}: {e}")
            yield art_item
