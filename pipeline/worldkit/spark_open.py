"""Muse Spark Open (open weights) served by Baseten, as an optional backend for the AI Edit verification step only.

    python -m worldkit.edit RUN_DIR "instruction" --spark-backend baseten     # off by default; the default stays the Meta Model API

Same call shape as ``meta_api.ask_muse_spark_with_usage`` (images + prompt -> (text, usage)), so ``edit.verify`` can use it unchanged.
Baseten's endpoint is OpenAI-compatible. The key is BASETEN_API_KEY in pipeline/.env (gitignored; never printed). Calls count against
the "spark" budget of ``worldkit.budget`` like Muse Spark calls.
"""
from __future__ import annotations

import os
from pathlib import Path

from openai import APIConnectionError, APIStatusError, OpenAI

from .meta_api import MetaAPIError, MetaAPIUnavailable, _env_path, image_to_data_url

BASETEN_BASE_URL = "https://inference.baseten.co/v1"
SPARK_OPEN_MODEL = "meta-models/muse-spark-open-1026"
MAX_TOKENS = 16000  # it reasons before answering: 200 tokens were not enough for a one-word reply, and one verification
# (the drill_hole edit) used all of 6000 and returned nothing (docs/results/spark_open_agreement.json, earlier_attempts)


def load_baseten_key() -> str:
    key = os.environ.get("BASETEN_API_KEY", "").strip()
    if not key:
        env = _env_path()
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "BASETEN_API_KEY":
                    key = v.strip().strip('"').strip("'")
    if not key:
        raise MetaAPIUnavailable("BASETEN_API_KEY is not set (pipeline/.env); Muse Spark Open on Baseten is unavailable")
    return key


def get_baseten_client() -> OpenAI:
    return OpenAI(base_url=BASETEN_BASE_URL, api_key=load_baseten_key())


def ask_spark_open_with_usage(images: list[Path], prompt: str, *, model: str = SPARK_OPEN_MODEL, client: OpenAI | None = None, max_tokens: int = MAX_TOKENS) -> tuple[str, dict | None]:
    """One images + prompt request to Muse Spark Open on Baseten. Returns (text, usage). Auth/billing errors -> MetaAPIUnavailable."""
    from . import budget

    try:
        budget.take("spark", prompt)
    except budget.BudgetExceeded as exc:
        raise MetaAPIUnavailable(str(exc)) from exc
    client = client or get_baseten_client()
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(img)}})
    try:
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}], max_tokens=max_tokens)
    except APIStatusError as exc:
        if exc.status_code in (401, 402, 403):
            raise MetaAPIUnavailable(f"Baseten rejected the request ({model}): HTTP {exc.status_code}") from exc
        raise MetaAPIError(f"Baseten request failed ({model}): HTTP {exc.status_code} - {exc.message}") from exc
    except APIConnectionError as exc:
        raise MetaAPIError(f"Could not reach Baseten at {BASETEN_BASE_URL}: {exc}") from exc
    usage = None
    if getattr(resp, "usage", None) is not None:
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
    return resp.choices[0].message.content or "", usage
