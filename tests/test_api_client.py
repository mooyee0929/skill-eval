import io
import json
from pathlib import Path

import pytest

from skill_eval.api_client import ApiError, complete
from skill_eval.config import ApiSpec, load_eval_config
from tests.test_config import VALID, write_config


class FakeResponse(io.BytesIO):
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return False


def test_anthropic_request_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse(json.dumps({
            "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("skill_eval.api_client.urllib.request.urlopen", fake_urlopen)
    spec = ApiSpec()
    text, usage = complete(spec, prompt="hi", system="RULES", model="m1", timeout_s=30)
    assert text == "hello world"
    assert usage == {"input_tokens": 10, "output_tokens": 5}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["body"]["system"] == "RULES"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["headers"].get("X-api-key") == "sk-test"


def test_openai_request_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["body"] = json.loads(request.data.decode())
        captured["url"] = request.full_url
        return FakeResponse(json.dumps({
            "choices": [{"message": {"content": "answer"}}],
        }).encode())

    monkeypatch.setenv("GATEWAY_KEY", "gk-1")
    monkeypatch.setattr("skill_eval.api_client.urllib.request.urlopen", fake_urlopen)
    spec = ApiSpec(kind="openai", base_url="http://gw.internal:8080", api_key_env="GATEWAY_KEY")
    text, usage = complete(spec, prompt="hi", system=None, model="m2", timeout_s=30)
    assert text == "answer"
    assert usage is None
    assert captured["url"] == "http://gw.internal:8080/v1/chat/completions"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ApiError, match="ANTHROPIC_API_KEY"):
        complete(ApiSpec(), prompt="x", system=None, model="m", timeout_s=5)


def test_api_executor_config(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("# s", encoding="utf-8")
    body = VALID.format() + """\
executor:
  api:
    kind: openai
    base_url: http://gw.internal:8080
    api_key_env: GATEWAY_KEY
"""
    cfg = load_eval_config(write_config(tmp_path, body))
    assert cfg.executor.api is not None
    assert cfg.executor.api.kind == "openai"
    assert cfg.executor.api.api_key_env == "GATEWAY_KEY"


def test_api_and_run_cmd_mutually_exclusive(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("# s", encoding="utf-8")
    body = VALID.format() + """\
executor:
  run_cmd: ["x", "-p", "{prompt}"]
  api:
    kind: anthropic
"""
    with pytest.raises(ValueError, match="mutually exclusive"):
        load_eval_config(write_config(tmp_path, body))
