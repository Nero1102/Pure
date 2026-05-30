import json

from pure.core.models import OpenAICompatibleModelClient


class FakeResponse:
    def __init__(self, body, content_type="application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_openai_compatible_json_response_extracts_text_and_usage_metadata(monkeypatch):
    payload = {
        "output_text": "JSON extracted text",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 40},
        },
    }

    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        temperature=0.2,
        timeout=10,
    )

    text = client.complete("hello", 64, prompt_cache_key="prefix-key", prompt_cache_retention="in_memory")

    assert text == "JSON extracted text"
    assert client.last_completion_metadata["input_tokens"] == 120
    assert client.last_completion_metadata["output_tokens"] == 30
    assert client.last_completion_metadata["total_tokens"] == 150
    assert client.last_completion_metadata["cached_tokens"] == 40
    assert client.last_completion_metadata["cache_hit"] is True


def test_openai_compatible_sse_response_extracts_text_and_usage_metadata(monkeypatch):
    sse_body = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"Hello "}',
            'data: {"type":"response.completed","response":{"output_text":"Hello SSE","usage":{"input_tokens":99,"output_tokens":11,"total_tokens":110,"input_tokens_details":{"cached_tokens":5}}}}',
            "data: [DONE]",
        ]
    )

    def fake_urlopen(request, timeout):
        return FakeResponse(sse_body, content_type="text/event-stream")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        temperature=0.2,
        timeout=10,
    )

    text = client.complete("hello", 64, prompt_cache_key="prefix-key", prompt_cache_retention="in_memory")

    assert text == "Hello SSE"
    assert client.last_completion_metadata["input_tokens"] == 99
    assert client.last_completion_metadata["output_tokens"] == 11
    assert client.last_completion_metadata["total_tokens"] == 110
    assert client.last_completion_metadata["cached_tokens"] == 5
    assert client.last_completion_metadata["cache_hit"] is True
