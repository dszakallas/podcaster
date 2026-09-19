import asyncio
import contextlib
import logging
import multiprocessing
import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import langcodes
from google.cloud import storage
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from .config import GCPConfig, PodcastTranscriptionConfig
from .models import PodcastGenArtifact, TaskStatus, TranscriptionTask
from .utils import PollingJob, PollStatus

logger = logging.getLogger(__name__)

MAX_POLL_TIMEOUT_SECONDS = 1800.0

LANGUAGE_EXCEPTIONS: dict[str, str] = {
    "ar": "ar-SA",
    "pt": "pt-PT",
    "sw": "sw-KE",
}


def to_bcp47(lang_code: str) -> str:
    """Resolve a language code to a BCP-47 tag with territory (e.g., 'en' -> 'en-US')."""
    if lang_code in LANGUAGE_EXCEPTIONS:
        return LANGUAGE_EXCEPTIONS[lang_code]
    try:
        lang = langcodes.Language.get(lang_code)
        maxed = lang.maximize()
        if maxed.territory:
            return langcodes.Language(
                language=maxed.language, territory=maxed.territory
            ).to_tag()
        return lang.to_tag()
    except Exception:
        return lang_code


def preprocess_audio(input_path: str, output_path: str, speed_factor: float = 1.5):
    """Runs ffmpeg to convert to mono and apply speedup."""
    # -y to overwrite output if exists
    # -vn to disable video (ignore cover art)
    # -ac 1 for mono
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-af",
        f"atempo={speed_factor}",
        "-ac",
        "1",
        output_path,
    ]
    subprocess.run(
        cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return output_path


async def upload_to_gcs(
    local_path: str, bucket_name: str, destination_blob_name: str
) -> str:
    """Uploads a file to GCS and returns the gs:// URI."""
    loop = asyncio.get_running_loop()

    def _upload():
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(local_path)
        return f"gs://{bucket_name}/{destination_blob_name}"

    return await loop.run_in_executor(None, _upload)


async def delete_from_gcs(gcs_uri: str):
    """Deletes a file from GCS."""
    if not gcs_uri.startswith("gs://"):
        return

    path = gcs_uri[5:]
    bucket_name, blob_name = path.split("/", 1)

    loop = asyncio.get_running_loop()

    def _delete():
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()

    await loop.run_in_executor(None, _delete)


async def create_transcription_jobs(
    artifacts: AsyncIterable[PodcastGenArtifact],
    gcp_config: GCPConfig,
    transcription_config: PodcastTranscriptionConfig,
) -> AsyncGenerator[TranscriptionTask, None]:
    speed_factor = transcription_config.speed_factor
    project_id = gcp_config.project_id
    location = gcp_config.location
    bucket_name = gcp_config.gcs_bucket

    if not project_id or not bucket_name:
        raise ValueError(
            "GCP project_id and gcs_bucket must be configured in podcaster.yaml"
        )

    client = SpeechClient(
        client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
    )

    # Background thread pool for ffmpeg (avoiding forks which break gRPC)
    num_workers = max(1, multiprocessing.cpu_count() // 4)
    executor = ThreadPoolExecutor(max_workers=num_workers)
    loop = asyncio.get_running_loop()

    async for art_item in artifacts:
        local_path = art_item.path
        if not local_path or not os.path.exists(local_path):
            logger.error(f"File not found for transcription: {local_path}")
            continue

        metadata = art_item.metadata
        gen_podcast_meta = metadata.get("generate-podcast", {})
        lang_code = gen_podcast_meta.get("language", "en")
        bcp47_lang = to_bcp47(lang_code)

        artifact_id = art_item.artifact_id

        gcs_uri = None
        # Use a temporary file for local preprocessing output
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            preprocessed_path = tmp_file.name

        try:
            # 1. Preprocess with ffmpeg (mono, speedup, convert to WAV for compatibility)
            logger.info(
                f"Preprocessing {local_path} ({speed_factor}x speed, mono, wav)..."
            )
            await loop.run_in_executor(
                executor,
                preprocess_audio,
                local_path,
                preprocessed_path,
                speed_factor,
            )

            # 2. Upload to GCS
            dest_name = (
                f"transcriptions/{uuid.uuid4().hex}_{os.path.basename(local_path)}.wav"
            )
            logger.info(
                f"Uploading preprocessed audio to gs://{bucket_name}/{dest_name}..."
            )
            gcs_uri = await upload_to_gcs(preprocessed_path, bucket_name, dest_name)

            # 3. Create async batch recognition request
            logger.info(
                f"Submitting BatchRecognize request for {gcs_uri} (language: {bcp47_lang})..."
            )
            parent = f"projects/{project_id}/locations/{location}"
            rec_config = cloud_speech.RecognitionConfig(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
                language_codes=[bcp47_lang],
                model="chirp_2",
                features=cloud_speech.RecognitionFeatures(
                    enable_word_time_offsets=True,
                ),
            )
            file_metadata = cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)
            request = cloud_speech.BatchRecognizeRequest(
                recognizer=f"{parent}/recognizers/_",
                config=rec_config,
                files=[file_metadata],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    inline_response_config=cloud_speech.InlineOutputConfig(),
                ),
            )
            operation = client.batch_recognize(request=request)
            operation_id = operation.operation.name
            logger.info(f"Started BatchRecognize operation: {operation_id}")

            yield TranscriptionTask(
                artifact_id=artifact_id,
                task_id=operation_id,
                gcs_uri=gcs_uri,
                path=local_path,
                preprocessed_path=preprocessed_path,
                bcp47_lang=bcp47_lang or "en-US",
                speed_factor=speed_factor,
                status=TaskStatus.PENDING,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Failed to start transcription for {local_path}: {e}")
            if preprocessed_path and os.path.exists(preprocessed_path):
                with contextlib.suppress(Exception):
                    os.remove(preprocessed_path)
            if gcs_uri:
                with contextlib.suppress(Exception):
                    await delete_from_gcs(gcs_uri)
            raise

    executor.shutdown()


async def poll_transcription_jobs(
    tasks: AsyncIterable[TranscriptionTask],
    gcp_config: GCPConfig,
) -> AsyncGenerator[TranscriptionTask, None]:
    from google.api_core import operation as api_operation
    from google.longrunning import operations_pb2

    location = gcp_config.location

    client = SpeechClient(
        client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
    )

    async def check_status(t: TranscriptionTask) -> PollStatus:
        logger.info(f"Polling transcription operation: {t.task_id}")
        gapic_op = client.get_operation(
            request=operations_pb2.GetOperationRequest(name=t.task_id)
        )
        op = api_operation.from_gapic(
            gapic_op,
            client.transport.operations_client,
            cloud_speech.BatchRecognizeResponse,
        )
        if op.done():
            try:
                op.result()
                return PollStatus(status=TaskStatus.COMPLETED)
            except Exception as e:
                return PollStatus(status=TaskStatus.FAILED, error=str(e))
        return PollStatus(status=TaskStatus.IN_PROGRESS)

    polling_job = PollingJob(
        check_status=check_status,
        timeout=MAX_POLL_TIMEOUT_SECONDS,
        interval=10.0,
        sleep=asyncio.sleep,
    )
    async for t in polling_job.poll_stream(tasks):
        yield t


async def download_transcription_jobs(
    tasks: AsyncIterable[TranscriptionTask],
    gcp_config: GCPConfig,
) -> AsyncGenerator[TranscriptionTask, None]:
    from google.api_core import operation as api_operation
    from google.longrunning import operations_pb2

    location = gcp_config.location

    client = SpeechClient(
        client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
    )

    async for t in tasks:
        if t.status != TaskStatus.COMPLETED:
            logger.warning(
                f"Skipping download for transcription task {t.task_id} as status is not completed"
            )
            continue

        task_id = t.task_id
        local_path = t.path
        if not local_path:
            raise ValueError(f"Task {t.task_id} is missing local path.")
        preprocessed_path = t.preprocessed_path
        gcs_uri = t.gcs_uri
        speed_factor = t.speed_factor
        bcp47_lang = t.bcp47_lang
        metadata = t.metadata.copy()

        try:
            gapic_op = client.get_operation(
                request=operations_pb2.GetOperationRequest(name=task_id)
            )
            op = api_operation.from_gapic(
                gapic_op,
                client.transport.operations_client,
                cloud_speech.BatchRecognizeResponse,
            )

            response = op.result()
            if not response or not getattr(response, "results", None):
                raise RuntimeError("No response or results returned from Speech API.")

            result = response.results.get(gcs_uri or "")
            if not result:
                raise RuntimeError("No result returned for this file.")

            if result.error.code != 0:
                error_msg = result.error.message or f"Error code {result.error.code}"
                raise RuntimeError(f"Transcription failed: {error_msg}")

            transcript_json_path = os.path.splitext(local_path)[0] + ".tr.json"
            response_json = cloud_speech.BatchRecognizeResponse.to_json(response)
            with open(transcript_json_path, "w") as f:
                f.write(response_json)

            lrc_path = os.path.splitext(local_path)[0] + ".lrc"
            generate_lrc(response, lrc_path, speed_factor=speed_factor)
            logger.info("Transcription and lyrics saved.")

            metadata["transcribe-podcast"] = {
                "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": "chirp_2",
                "preprocessed": True,
                "speed_factor": speed_factor,
                "language": bcp47_lang,
                "lrc_path": lrc_path,
            }

            yield t.model_copy(
                update={
                    "transcript_path": transcript_json_path,
                    "lrc_path": lrc_path,
                    "metadata": metadata,
                    "status": TaskStatus.COMPLETED,
                }
            )

        except Exception as e:
            logger.error(
                f"Failed to process transcription results for {local_path}: {e}",
                exc_info=True,
            )
        finally:
            if gcs_uri:
                try:
                    await delete_from_gcs(gcs_uri)
                except Exception as e:
                    logger.warning(f"Failed to delete staging file from GCS: {e}")
            if preprocessed_path and os.path.exists(preprocessed_path):
                with contextlib.suppress(Exception):
                    os.remove(preprocessed_path)


async def transcribe_artifacts(
    artifacts: AsyncIterable[PodcastGenArtifact],
    gcp_config: GCPConfig,
    transcription_config: PodcastTranscriptionConfig,
) -> AsyncGenerator[TranscriptionTask, None]:
    jobs = create_transcription_jobs(
        artifacts,
        gcp_config=gcp_config,
        transcription_config=transcription_config,
    )
    polled = poll_transcription_jobs(jobs, gcp_config=gcp_config)
    async for result in download_transcription_jobs(polled, gcp_config=gcp_config):
        yield result


def _duration_to_seconds(offset: Any) -> float:
    if offset is None:
        return 0.0
    if hasattr(offset, "total_seconds"):
        return float(offset.total_seconds())
    return float(getattr(offset, "seconds", 0) + getattr(offset, "nanos", 0) / 1e9)


def generate_lrc(
    response: cloud_speech.BatchRecognizeResponse,
    output_path: str,
    speed_factor: float = 1.0,
) -> None:
    """Generates an LRC file, scaling timestamps by speed_factor."""
    lines: list[str] = []

    logger.info(f"Generating LRC for {len(response.results)} files in response...")
    for file_uri, file_result in response.results.items():
        logger.debug(f"Processing file result for {file_uri}")
        if file_result.error.code != 0:
            logger.warning(
                f"Skipping {file_uri} due to error: {file_result.error.message}"
            )
            continue

        transcript = file_result.transcript
        if not transcript or not transcript.results:
            logger.debug(f"No transcript or results for {file_uri}")
            continue

        for result in transcript.results:
            if not result.alternatives:
                continue

            alt = result.alternatives[0]
            if not alt.words:
                lines.append(f"[00:00.00]{alt.transcript}")
                continue

            curr_words: list[str] = []
            curr_start: float | None = None

            for word in alt.words:
                seconds = _duration_to_seconds(getattr(word, "start_offset", None))

                if curr_start is None:
                    curr_start = seconds

                # If we have reached the 2s threshold, flush the current group
                if seconds - curr_start >= 2.0 and curr_words:
                    original_seconds = curr_start * speed_factor
                    ts = f"[{int(original_seconds // 60):02d}:{original_seconds % 60:05.2f}]"
                    lines.append(f"{ts}{' '.join(curr_words)}")
                    curr_words = [word.word]
                    curr_start = seconds
                else:
                    curr_words.append(word.word)

            # Flush final group for this result
            if curr_words and curr_start is not None:
                original_seconds = curr_start * speed_factor
                ts = (
                    f"[{int(original_seconds // 60):02d}:{original_seconds % 60:05.2f}]"
                )
                lines.append(f"{ts}{' '.join(curr_words)}")

    logger.info(f"Writing {len(lines)} lines to {output_path}")
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
