import os
import datetime
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TALB, TRCK, TDRC, TPE1, TPE2, COMM, WOAR, TLAN, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import Picture
import mimetypes

def set_mp3_tags(path, out_path, title, album, track, date, artists, album_artist, source, cover_path, language):
    try:
        audio = MP3(path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(path)
        audio.add_tags()
    
    tags = audio.tags

    if title: tags.add(TIT2(encoding=3, text=title))
    if album: tags.add(TALB(encoding=3, text=album))
    if track: tags.add(TRCK(encoding=3, text=str(track)))
    if date: tags.add(TDRC(encoding=3, text=date))
    if artists: tags.add(TPE1(encoding=3, text="/".join(artists)))
    if album_artist: tags.add(TPE2(encoding=3, text=album_artist))
    if source: tags.add(WOAR(url=source))
    if language: tags.add(TLAN(encoding=3, text=language))

    if cover_path:
        with open(cover_path, 'rb') as f:
            mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
            tags.add(APIC(
                encoding=3,
                mime=mime,
                type=3, # 3 is for the album front cover
                desc='Cover',
                data=f.read()
            ))
    
    audio.save(out_path)

def set_mp4_tags(path, out_path, title, album, track, date, artists, album_artist, source, cover_path, language):
    # For m4a
    audio = MP4(path)
    
    if title: audio["\xa9nam"] = title
    if album: audio["\xa9alb"] = album
    if track: audio["trkn"] = [(int(track), 0)]
    if date: audio["\xa9day"] = date
    if artists: audio["\xa9ART"] = artists
    if album_artist: audio["aART"] = album_artist
    if source: audio["\xa9cmt"] = f"Source: {source}" # M4A doesn't have a standard URL tag, using comment
    if language: audio["\xa9lan"] = language

    if cover_path:
        with open(cover_path, 'rb') as f:
            cover_data = f.read()
            fmt = MP4Cover.FORMAT_JPEG
            if cover_path.lower().endswith((".png")):
                fmt = MP4Cover.FORMAT_PNG
            audio["covr"] = [MP4Cover(cover_data, imageformat=fmt)]
    
    audio.save(out_path)

def set_ogg_tags(path, out_path, title, album, track, date, artists, album_artist, source, cover_path, language):
    audio = OggVorbis(path)
    
    if title: audio["title"] = title
    if album: audio["album"] = album
    if track: audio["tracknumber"] = str(track)
    if date: audio["date"] = date
    if artists: audio["artist"] = artists
    if album_artist: audio["albumartist"] = album_artist
    if source: audio["website"] = source
    if language: audio["language"] = language

    if cover_path:
        picture = Picture()
        with open(cover_path, "rb") as f:
            picture.data = f.read()
        picture.type = 3 # Front cover
        picture.mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
        picture.desc = "Cover"
        
        import base64
        picture_data = base64.b64encode(picture.write()).decode('ascii')
        audio["metadata_block_picture"] = [picture_data]

    audio.save(out_path)

def tag_file(audio_file, cover, title, album, track, date, artists, album_artist, source, in_place, out, language):
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
    
    if ext == '.mp3':
        set_mp3_tags(audio_file, out_path, title, album, track, date, artists, album_artist, source, cover, language)
    elif ext in ('.m4a', '.mp4'):
        set_mp4_tags(audio_file, out_path, title, album, track, date, artists, album_artist, source, cover, language)
    elif ext == '.ogg':
        set_ogg_tags(audio_file, out_path, title, album, track, date, artists, album_artist, source, cover, language)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    return out_path
