import asyncio
import logging
import multiprocessing
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, AsyncIterable

from google.cloud import storage
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from .config import GCPConfig, PodcastTranscriptionConfig
from .models import PodcastGenArtifact, TaskStatus, TranscriptionTask
from .utils import is_transient_network_exception

logger = logging.getLogger(__name__)

MAX_POLL_TIMEOUT_SECONDS = 1800.0

# Simple mapping for common languages to BCP-47
LANGUAGE_MAP = {
    "af": "af-ZA",
    "am": "am-ET",
    "ar": "ar-SA",
    "az": "az-AZ",
    "bg": "bg-BG",
    "bn": "bn-BD",
    "ca": "ca-ES",
    "cs": "cs-CZ",
    "da": "da-DK",
    "de": "de-DE",
    "el": "el-GR",
    "en": "en-US",
    "es": "es-ES",
    "et": "et-EE",
    "eu": "eu-ES",
    "fa": "fa-IR",
    "fi": "fi-FI",
    "fr": "fr-FR",
    "gl": "gl-ES",
    "gu": "gu-IN",
    "he": "he-IL",
    "hi": "hi-IN",
    "hr": "hr-HR",
    "hu": "hu-HU",
    "id": "id-ID",
    "is": "is-IS",
    "it": "it-IT",
    "ja": "ja-JP",
    "jv": "jv-ID",
    "kn": "kn-IN",
    "ko": "ko-KR",
    "lt": "lt-LT",
    "lv": "lv-LV",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "ms": "ms-MY",
    "nl": "nl-NL",
    "no": "no-NO",
    "pl": "pl-PL",
    "pt": "pt-PT",
    "ro": "ro-RO",
    "ru": "ru-RU",
    "sk": "sk-SK",
    "sl": "sl-SI",
    "sq": "sq-AL",
    "sr": "sr-RS",
    "sv": "sv-SE",
    "sw": "sw-KE",
    "ta": "ta-IN",
    "te": "te-IN",
    "th": "th-TH",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "ur": "ur-PK",
    "vi": "vi-VN",
    "zh": "zh-CN",
    "zu": "zu-ZA",
}


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
        bcp47_lang = LANGUAGE_MAP.get(lang_code, lang_code)

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
                try:
                    os.remove(preprocessed_path)
                except Exception:
                    pass
            if gcs_uri:
                try:
                    await delete_from_gcs(gcs_uri)
                except Exception:
                    pass

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

    async for t in tasks:
        task_id = t.task_id
        logger.info(f"Polling transcription operation: {task_id}")
        started_at = time.time()
        while True:
            try:
                gapic_op = client.get_operation(
                    request=operations_pb2.GetOperationRequest(name=task_id)
                )
                op = api_operation.from_gapic(
                    gapic_op,
                    client.transport.operations_client,
                    cloud_speech.BatchRecognizeResponse,
                )
                if op.done():
                    try:
                        # Call result to verify it completed without error
                        op.result()
                        yield t.model_copy(update={"status": TaskStatus.COMPLETED})
                    except Exception as e:
                        yield t.model_copy(
                            update={"status": TaskStatus.FAILED, "error": str(e)}
                        )
                    break
            except Exception as e:
                if not is_transient_network_exception(e):
                    logger.error(
                        f"Non-retryable error polling transcription task {task_id}: {e}"
                    )
                    yield t.model_copy(
                        update={"status": TaskStatus.FAILED, "error": str(e)}
                    )
                    break

                logger.warning(
                    f"Transient network error polling transcription task {task_id}: {e}"
                )

            elapsed = time.time() - started_at
            if elapsed > MAX_POLL_TIMEOUT_SECONDS:
                logger.error(
                    f"Polling transcription task {task_id} timed out after {elapsed:.1f}s"
                )
                yield t.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "error": f"Transcription task {task_id} timed out after {elapsed:.1f}s",
                    }
                )
                break

            await asyncio.sleep(10)


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
                try:
                    os.remove(preprocessed_path)
                except Exception:
                    pass


async def transcribe_artifacts(
    artifacts: AsyncIterable[PodcastGenArtifact],
    gcp_config: GCPConfig,
    transcription_config: PodcastTranscriptionConfig,
) -> AsyncGenerator[TranscriptionTask, None]:
    # 1. Create jobs
    tasks = []
    async for task in create_transcription_jobs(
        artifacts,
        gcp_config=gcp_config,
        transcription_config=transcription_config,
    ):
        tasks.append(task)

    # 2. Poll jobs
    async def tasks_gen():
        for t in tasks:
            yield t

    completed = []
    async for comp in poll_transcription_jobs(tasks_gen(), gcp_config=gcp_config):
        completed.append(comp)

    # 3. Download results
    async def completed_gen():
        for c in completed:
            yield c

    async for result in download_transcription_jobs(
        completed_gen(), gcp_config=gcp_config
    ):
        yield result


def generate_lrc(
    response: cloud_speech.BatchRecognizeResponse,
    output_path: str,
    speed_factor: float = 1.0,
):
    """Generates an LRC file, scaling timestamps by speed_factor."""
    lines = []

    logger.info(f"Generating LRC for {len(response.results)} files in response...")
    for file_uri, file_result in response.results.items():
        logger.debug(f"Processing file result for {file_uri}")
        # result.error is a google.rpc.Status
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

            curr_words = []
            curr_start = None

            for word in alt.words:
                # Handle start_offset
                start_offset = (
                    word.start_offset if hasattr(word, "start_offset") else None
                )
                if start_offset is None:
                    seconds = 0.0
                elif hasattr(start_offset, "total_seconds"):
                    seconds = float(getattr(start_offset, "total_seconds")())
                else:
                    seconds = (
                        getattr(start_offset, "seconds", 0)
                        + getattr(start_offset, "nanos", 0) / 1e9
                    )

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
            if curr_words:
                assert curr_start is not None
                original_seconds = curr_start * speed_factor
                ts = (
                    f"[{int(original_seconds // 60):02d}:{original_seconds % 60:05.2f}]"
                )
                lines.append(f"{ts}{' '.join(curr_words)}")

    logger.info(f"Writing {len(lines)} lines to {output_path}")
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
