import asyncio
import logging
import multiprocessing
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from google.cloud import storage
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from .utils import load_config, setup_logging

logger = logging.getLogger(__name__)

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
    artifacts: AsyncGenerator[dict, None],
    verbose: bool = False,
    transcriber_key: str = "default",
) -> AsyncGenerator[dict, None]:
    if verbose:
        setup_logging(verbose)

    config_data = load_config()
    gcp_config = config_data.gcp
    from .config import PodcastTranscriptionConfig

    if transcriber_key not in config_data.podcast_transcribers:
        logger.warning(
            f"Podcast transcriber '{transcriber_key}' not found in configuration. Using default."
        )
        transcription_config = config_data.podcast_transcribers.get(
            "default", PodcastTranscriptionConfig()
        )
    else:
        transcription_config = config_data.podcast_transcribers[transcriber_key]

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

    async for art in artifacts:
        local_path = art.get("path")
        if not local_path or not os.path.exists(local_path):
            logger.error(f"File not found for transcription: {local_path}")
            continue

        metadata = art.get("metadata", {})
        gen_podcast_meta = metadata.get("generate-podcast", {})
        lang_code = gen_podcast_meta.get("language", "en")
        bcp47_lang = LANGUAGE_MAP.get(lang_code, lang_code)

        artifact_id = art.get("artifact_id", "unknown")

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
                executor, preprocess_audio, local_path, preprocessed_path, speed_factor
            )

            # 2. Upload to GCS with random suffix
            random_suffix = uuid.uuid4().hex[:8]
            gcs_blob_name = f"transcription_staging/{artifact_id}_{random_suffix}.wav"
            logger.info(f"Uploading preprocessed file to GCS: {gcs_blob_name}...")
            gcs_uri = await upload_to_gcs(preprocessed_path, bucket_name, gcs_blob_name)

            # 3. Prepare transcription config
            recognition_config = cloud_speech.RecognitionConfig(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
                language_codes=[bcp47_lang],
                model="chirp_2",
                features=cloud_speech.RecognitionFeatures(
                    enable_word_time_offsets=True,
                    enable_automatic_punctuation=True,
                ),
            )

            request = cloud_speech.BatchRecognizeRequest(
                recognizer=f"projects/{project_id}/locations/{location}/recognizers/_",
                config=recognition_config,
                files=[cloud_speech.BatchRecognizeFileMetadata(uri=gcs_uri)],
                recognition_output_config=cloud_speech.RecognitionOutputConfig(
                    inline_response_config=cloud_speech.InlineOutputConfig(),
                ),
                processing_strategy=cloud_speech.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING,
            )

            # 4. Execute operation creation
            logger.info(f"Transcribing {local_path} (Batch Chirp 2)...")
            operation = client.batch_recognize(request=request)

            yield {
                **art,
                "task_id": operation.operation.name,
                "preprocessed_path": preprocessed_path,
                "gcs_uri": gcs_uri,
                "bcp47_lang": bcp47_lang,
                "speed_factor": speed_factor,
                "status": "pending",
                "type": "transcription",
            }

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
    tasks: AsyncGenerator[dict, None],
) -> AsyncGenerator[dict, None]:
    from google.api_core import operation as api_operation
    from google.longrunning import operations_pb2

    config_data = load_config()
    gcp_config = config_data.gcp
    location = gcp_config.location

    client = SpeechClient(
        client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
    )

    async for task in tasks:
        task_id = task["task_id"]
        logger.info(f"Polling transcription operation: {task_id}")
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
                        yield {**task, "status": "completed"}
                    except Exception as e:
                        yield {**task, "status": "failed", "error": str(e)}
                    break
            except Exception as e:
                logger.error(f"Error polling transcription: {e}")

            await asyncio.sleep(10)


async def download_transcription_jobs(
    tasks: AsyncGenerator[dict, None], verbose: bool = False
) -> AsyncGenerator[dict, None]:
    from google.api_core import operation as api_operation
    from google.longrunning import operations_pb2

    config_data = load_config()
    gcp_config = config_data.gcp
    location = gcp_config.location

    client = SpeechClient(
        client_options={"api_endpoint": f"{location}-speech.googleapis.com"}
    )

    async for task in tasks:
        if task.get("status") != "completed":
            logger.warning(
                f"Skipping download for transcription task {task['task_id']} as status is not completed"
            )
            continue

        task_id = task["task_id"]
        local_path = task.get("path")
        preprocessed_path = task.get("preprocessed_path")
        gcs_uri = task.get("gcs_uri")
        speed_factor = task.get("speed_factor", 1.0)
        bcp47_lang = task.get("bcp47_lang", "en-US")
        metadata = task.get("metadata", {})

        try:
            gapic_op = client.get_operation(
                request=operations_pb2.GetOperationRequest(name=task_id)
            )
            op = api_operation.from_gapic(
                gapic_op,
                client.transport.operations_client,
                cloud_speech.BatchRecognizeResponse,
            )

            # Get deserialized response
            response = op.result()

            result = response.results.get(gcs_uri)
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

            yield {
                **task,
                "transcript_path": transcript_json_path,
                "lrc_path": lrc_path,
                "metadata": metadata,
                "status": "downloaded",
            }

        except Exception as e:
            logger.error(
                f"Failed to process transcription results for {local_path}: {e}"
            )
            if verbose:
                import traceback

                traceback.print_exc()
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
    artifacts: AsyncGenerator[dict, None],
    verbose: bool = False,
    transcriber_key: str = "default",
) -> AsyncGenerator[dict, None]:
    # 1. Create jobs
    tasks = []
    async for task in create_transcription_jobs(
        artifacts, verbose, transcriber_key=transcriber_key
    ):
        tasks.append(task)

    # 2. Poll jobs
    async def tasks_gen():
        for t in tasks:
            yield t

    completed = []
    async for comp in poll_transcription_jobs(tasks_gen()):
        completed.append(comp)

    # 3. Download results
    async def completed_gen():
        for c in completed:
            yield c

    async for result in download_transcription_jobs(completed_gen(), verbose):
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
                    seconds = start_offset.total_seconds()
                else:
                    seconds = start_offset.seconds + start_offset.nanos / 1e9

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
