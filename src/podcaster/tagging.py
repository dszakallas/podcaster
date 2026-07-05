import base64
import datetime
import logging
import mimetypes
import os
import time
from typing import AsyncGenerator, Optional

from mutagen.flac import Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TDRC,
    TIT2,
    TLAN,
    TPE1,
    TPE2,
    TRCK,
    WOAR,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

from .utils import load_config

logger = logging.getLogger(__name__)


def set_mp3_tags(
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
    try:
        audio = MP3(path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(path)
        audio.add_tags()

    tags = audio.tags

    if title:
        tags.add(TIT2(encoding=3, text=title))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if track:
        tags.add(TRCK(encoding=3, text=str(track)))
    if date:
        tags.add(TDRC(encoding=3, text=date))
    if artists:
        tags.add(TPE1(encoding=3, text="/".join(artists)))
    if album_artist:
        tags.add(TPE2(encoding=3, text=album_artist))
    if source:
        tags.add(WOAR(url=source))
    if language:
        tags.add(TLAN(encoding=3, text=language))

    if cover_path:
        with open(cover_path, "rb") as file_handle:
            mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
            tags.add(
                APIC(
                    encoding=3,
                    mime=mime,
                    type=3,
                    desc="Cover",
                    data=file_handle.read(),
                )
            )

    audio.save(out_path)


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
        audio["\xa9cmt"] = f"Source: {source}"
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


def set_ogg_tags(
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
    audio = OggVorbis(path)

    if title:
        audio["title"] = title
    if album:
        audio["album"] = album
    if track:
        audio["tracknumber"] = str(track)
    if date:
        audio["date"] = date
    if artists:
        audio["artist"] = artists
    if album_artist:
        audio["albumartist"] = album_artist
    if source:
        audio["website"] = source
    if language:
        audio["language"] = language

    if cover_path:
        picture = Picture()
        with open(cover_path, "rb") as file_handle:
            picture.data = file_handle.read()
        picture.type = 3
        picture.mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
        picture.desc = "Cover"

        picture_data = base64.b64encode(picture.write()).decode("ascii")
        audio["metadata_block_picture"] = [picture_data]

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

    if ext == ".mp3":
        set_mp3_tags(
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
    elif ext in (".m4a", ".mp4"):
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
    elif ext == ".ogg":
        set_ogg_tags(
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
        raise ValueError(f"Unsupported file extension: {ext}")

    return out_path


async def tag_artifacts(
    artifacts: AsyncGenerator[dict, None],
    cover_path: Optional[str] = None,
    track_offset: int = 0,
    album: Optional[str] = None,
    created_at: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    config = load_config()
    tags_config = config.podcast_tags
    default_album_artist = tags_config.album_artist
    default_artists = tags_config.artists

    auto_track_count = 0

    async for art in artifacts:
        notebook_id = art["notebook_id"]
        artifact_id = art["artifact_id"]
        title = art.get("title", artifact_id)
        out_path = art["path"]
        metadata = art.get("metadata", {})
        gen_podcast_meta = metadata.get("generate-podcast", {})
        language = gen_podcast_meta.get("language")
        source_url = f"https://notebooklm.google.com/notebook/{notebook_id}"

        album_val = album or art.get("album") or "NotebookLM Podcast"
        created_at_val = created_at or art.get("created_at")

        explicit_track = art.get("track")
        if explicit_track is not None:
            track_number = explicit_track
        else:
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

            yield {**art, "metadata": metadata, "track": track_number}
        except Exception as error:
            logger.debug(f"Tagging failed for {artifact_id}: {error}")
            yield art
