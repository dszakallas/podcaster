import datetime
from unittest.mock import MagicMock, patch

import pytest

from podcaster.config import PodcastTagsConfig
from podcaster.models import PodcastGenArtifact
from podcaster.tagging import AudioTags, set_mp4_tags, tag_artifacts, tag_file


def test_set_mp4_tags_sets_expected_atoms(tmp_path):
    tags = {}
    mock_mp4_instance = MagicMock()
    mock_mp4_instance.__getitem__.side_effect = tags.__getitem__
    mock_mp4_instance.__setitem__.side_effect = tags.__setitem__

    with (
        patch("podcaster.tagging.MP4", return_value=mock_mp4_instance) as mock_mp4_cls,
        patch("podcaster.tagging.MP4Cover") as mock_cover_cls,
    ):
        cover_file = tmp_path / "cover.png"
        cover_file.write_bytes(b"png_data")

        set_mp4_tags(
            path="input.m4a",
            out_path="output.m4a",
            tags=AudioTags(
                title="Test Episode",
                album="My Podcast",
                track=2,
                date="2026-09-19",
                artists=["Host A", "Host B"],
                album_artist="Publisher",
                source="https://example.com",
                cover_path=str(cover_file),
                language="en",
                total_tracks=5,
            ),
        )

        mock_mp4_cls.assert_called_once_with("input.m4a")
        assert tags["\xa9nam"] == "Test Episode"
        assert tags["\xa9alb"] == "My Podcast"
        assert tags["trkn"] == [(2, 5)]
        assert tags["\xa9day"] == "2026-09-19"
        assert tags["\xa9ART"] == ["Host A", "Host B"]
        assert tags["aART"] == "Publisher"
        assert tags["\xa9cmt"] == "https://example.com"
        assert tags["\xa9lan"] == "en"
        mock_mp4_instance.save.assert_called_once_with("output.m4a")
        mock_cover_cls.assert_called_once_with(
            b"png_data", imageformat=mock_cover_cls.FORMAT_PNG
        )


def test_tag_file_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported file extension: .mp3"):
        tag_file(
            audio_file="test.mp3",
            tags=AudioTags(
                title="Title",
                album="Album",
                track=1,
                artists=["Artist"],
                album_artist="Album Artist",
                source="Source",
            ),
            in_place=True,
        )


def test_tag_file_paths_and_defaults():
    with patch("podcaster.tagging.set_mp4_tags") as mock_set_tags:
        out = tag_file(
            audio_file="test.m4a",
            tags=AudioTags(
                title="Title",
                album="Album",
                track=1,
                artists=["Artist"],
                album_artist="Album Artist",
                source="Source",
                language="es",
            ),
            in_place=True,
        )
        assert out == "test.m4a"
        mock_set_tags.assert_called_once()
        args, kwargs = mock_set_tags.call_args
        passed_tags = args[2]
        assert passed_tags.date == datetime.datetime.now().strftime("%Y-%m-%d")


def test_tag_file_out_path_specified():
    with patch("podcaster.tagging.set_mp4_tags"):
        out = tag_file(
            audio_file="input.m4a",
            tags=AudioTags(
                title="Title",
                album="Album",
                track=1,
                date="2026-01-01",
                artists=["Artist"],
                album_artist="Album Artist",
                source="Source",
            ),
            in_place=False,
            out="custom_out.m4a",
        )
        assert out == "custom_out.m4a"


@pytest.mark.anyio
async def test_tag_artifacts_increments_track_number_and_updates_metadata():
    artifacts = [
        PodcastGenArtifact(
            path="file1.m4a",
            notebook_id="nb_1",
            artifact_id="art_1",
            filename="file1.m4a",
            title="Episode 1",
            metadata={"generate-podcast": {"language": "en"}},
        ),
        PodcastGenArtifact(
            path="file2.m4a",
            notebook_id="nb_1",
            artifact_id="art_2",
            filename="file2.m4a",
            title="Episode 2",
            metadata={"generate-podcast": {"language": "de"}},
        ),
    ]

    async def _iter_artifacts():
        for art in artifacts:
            yield art

    tags_config = PodcastTagsConfig(
        album_artist="Show Host",
        artists=["Host 1", "Host 2"],
    )

    with patch("podcaster.tagging.tag_file") as mock_tag_file:
        tagged_results = []
        async for item in tag_artifacts(
            _iter_artifacts(),
            cover_path="cover.jpg",
            track_offset=10,
            album="Custom Album",
            created_at="2026-09-18T12:00:00Z",
            tags_config=tags_config,
            total_tracks=12,
        ):
            tagged_results.append(item)

        assert len(tagged_results) == 2
        assert mock_tag_file.call_count == 2

        # Check first artifact
        first = tagged_results[0]
        assert first.metadata["tag-podcast"]["track"] == 11
        assert first.metadata["tag-podcast"]["total_tracks"] == 12
        assert first.metadata["tag-podcast"]["cover"] == "cover.jpg"

        # Check second artifact
        second = tagged_results[1]
        assert second.metadata["tag-podcast"]["track"] == 12
        assert second.metadata["tag-podcast"]["total_tracks"] == 12


@pytest.mark.anyio
async def test_tag_artifacts_preserves_explicit_track_number():
    artifacts = [
        PodcastGenArtifact(
            path="file1.m4a",
            notebook_id="nb_1",
            artifact_id="art_1",
            filename="file1.m4a",
            title="Part 1 (English)",
            metadata={"track": 5, "total_tracks": 10},
        ),
        PodcastGenArtifact(
            path="file2.m4a",
            notebook_id="nb_1",
            artifact_id="art_2",
            filename="file2.m4a",
            title="Part 1 (German)",
            metadata={"track": 5, "total_tracks": 10},
        ),
    ]

    async def _iter_artifacts():
        for art in artifacts:
            yield art

    tags_config = PodcastTagsConfig(
        album_artist="Show Host",
        artists=["Host 1"],
    )

    with patch("podcaster.tagging.tag_file") as mock_tag_file:
        tagged_results = []
        async for item in tag_artifacts(
            _iter_artifacts(),
            tags_config=tags_config,
        ):
            tagged_results.append(item)

        assert len(tagged_results) == 2
        # Both share explicit track number 5 (e.g. language variants of the same episode)
        assert tagged_results[0].metadata["tag-podcast"]["track"] == 5
        assert tagged_results[1].metadata["tag-podcast"]["track"] == 5
        assert mock_tag_file.call_count == 2


@pytest.mark.anyio
async def test_tag_artifacts_handles_error_gracefully():
    artifacts = [
        PodcastGenArtifact(
            path="error.m4a",
            notebook_id="nb_1",
            artifact_id="art_err",
            filename="error.m4a",
            title="Bad File",
            metadata={},
        ),
    ]

    async def _iter_artifacts():
        for art in artifacts:
            yield art

    tags_config = PodcastTagsConfig(
        album_artist="Show Host",
        artists=["Host 1"],
    )

    with patch(
        "podcaster.tagging.tag_file", side_effect=RuntimeError("Disk write failed")
    ):
        tagged_results = []
        async for item in tag_artifacts(
            _iter_artifacts(),
            tags_config=tags_config,
        ):
            tagged_results.append(item)

        assert len(tagged_results) == 1
        # Metadata should NOT have tag-podcast because it errored
        assert "tag-podcast" not in tagged_results[0].metadata
