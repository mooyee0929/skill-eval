"""Direct HTTP completion for environments without any agent CLI.

Supports the Anthropic Messages API and OpenAI-compatible chat endpoints
(internal gateways, Ollama, vLLM). stdlib urllib only — no extra deps, so
the zipapp stays self-contained. API mode is single-shot text generation:
no tools, no file access, so cases relying on check_cmd side effects will
not be exercised."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .config import ApiSpec


class ApiError(RuntimeError):
    pass


def complete(
    spec: ApiSpec,
    *,
    prompt: str,
    system: str | None,
    model: str,
    timeout_s: int,
) -> tuple[str, dict | None]:
    """Return (response_text, usage_dict_or_none)."""
    key = os.environ.get(spec.api_key_env, "")
    if not key:
        raise ApiError(f"environment variable {spec.api_key_env} is not set")

    if spec.kind == "anthropic":
        url = spec.base_url.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": model,
            "max_tokens": spec.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
    else:  # openai-compatible
        url = spec.base_url.rstrip("/") + "/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": messages, "max_tokens": spec.max_tokens}

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ApiError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach {url}: {exc.reason}") from exc

    if spec.kind == "anthropic":
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
    else:
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiError(f"unexpected response shape: {str(payload)[:300]}") from exc

    usage_raw = payload.get("usage") or {}
    usage = {k: v for k, v in usage_raw.items() if isinstance(v, int)} or None
    return text, usage
