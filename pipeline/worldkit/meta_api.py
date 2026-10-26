"""Shared client for the Meta Model API.

The Meta Model API (https://dev.meta.ai/docs) is drop-in compatible with the
OpenAI SDK: same base URL + bearer key pattern, same ``chat.completions`` and
``responses`` surfaces. We reuse the official ``openai`` Python package as the
transport and only add: key loading from ``.env``, retries with backoff on
transient failures, and error messages that say which model/call failed
instead of letting an SDK traceback leak out.

Model IDs (from the Meta Model API docs and meta-model-cookbook):
  - Muse Spark (object detection / vision reasoning): "muse-spark-1.3"
  - SAM 3.1 (segmentation):                            "sam-3.1"
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

META_API_BASE_URL = "https://api.meta.ai/v1"
MUSE_SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"

_RETRYABLE = (APIConnectionError, APIStatusError)
_AUTH_BILLING_STATUS_CODES = {401, 402, 403}


class MetaAPIError(RuntimeError):
    """Raised for any Meta Model API failure, with the originating call named."""


class MetaAPIUnavailable(MetaAPIError):
    """Raised specifically when the API is unusable for reasons the caller
    can legitimately work around with a local fallback: no key configured,
    or the key exists but the API rejected it for billing/auth reasons
    (HTTP 401/402/403). Any other failure (bad request, persistent 5xx,
    network down) is a real bug or outage and should NOT be silently
    papered over with a fallback -- it's raised as plain MetaAPIError.
    """


def _env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def has_api_key() -> bool:
    """Check whether MODEL_API_KEY is configured, without raising."""
    load_dotenv()
    env_path = _env_path()
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    return bool(os.environ.get("MODEL_API_KEY"))


def load_api_key() -> str:
    """Load MODEL_API_KEY from the environment / nearest .env file.

    Searches upward from the current working directory so this works whether
    you run commands from the repo root or from inside /pipeline.
    """
    load_dotenv()
    pipeline_env = _env_path()
    if pipeline_env.is_file():
        load_dotenv(pipeline_env, override=False)

    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise MetaAPIUnavailable(
            "MODEL_API_KEY is not set. Copy pipeline/.env.example to pipeline/.env "
            "and fill in your Meta Model API key, or export MODEL_API_KEY in your shell."
        )
    return key


def get_client() -> OpenAI:
    """Build an OpenAI-SDK client pointed at the Meta Model API."""
    return OpenAI(base_url=META_API_BASE_URL, api_key=load_api_key())


def image_to_data_url(path: Path) -> str:
    """Encode a local image file as a base64 data: URL for vision/SAM requests."""
    path = Path(path)
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1.5, min=1, max=20),
    retry=retry_if_exception_type(_RETRYABLE),
)
def _chat_completion_with_retry(client: OpenAI, **kwargs):
    return client.chat.completions.create(**kwargs)


def ask_muse_spark_with_usage(
    images: list[Path],
    prompt: str,
    *,
    model: str = MUSE_SPARK_MODEL,
    client: OpenAI | None = None,
) -> tuple[str, dict | None]:
    """Send one or more images plus a text prompt to Muse Spark.

    Returns (response_text, usage_dict_or_None). Raises MetaAPIUnavailable if
    there's no key or the API rejects it for billing/auth (401/402/403) --
    callers may fall back to a local heuristic in that case. Any other
    failure raises plain MetaAPIError and should stop the run.
    """
    client = client or get_client()
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(img)}})

    try:
        response = _chat_completion_with_retry(
            client,
            model=model,
            messages=[{"role": "user", "content": content}],
        )
    except APIStatusError as exc:
        if exc.status_code in _AUTH_BILLING_STATUS_CODES:
            raise MetaAPIUnavailable(
                f"Muse Spark rejected the request ({model}): HTTP {exc.status_code} - {exc.message}. "
                "Treating this as billing/auth unavailability."
            ) from exc
        raise MetaAPIError(
            f"Muse Spark request failed ({model}): HTTP {exc.status_code} - {exc.message}"
        ) from exc
    except APIConnectionError as exc:
        raise MetaAPIError(
            f"Could not reach the Meta Model API at {META_API_BASE_URL} for Muse Spark: {exc}"
        ) from exc

    usage = None
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else dict(response.usage)

    return response.choices[0].message.content or "", usage
