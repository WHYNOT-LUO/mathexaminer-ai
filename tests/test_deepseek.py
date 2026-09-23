import json
from types import SimpleNamespace

try:
    import httpx2 as httpx
except ImportError:
    import httpx
import openai
import pytest

from mathexaminer import deepseek

GOOD = {
    "marks": [{"label": "1", "mark_type": "M1", "max_marks": 2, "comment": "ok", "awarded": 2}],
    "summary": "Fine.",
    "topic": "Algebra",
    "key_takeaway": "Keep going.",
    "handwriting_confidence": 90,
}


def completion(payload=GOOD, finish="stop", text=None):
    body = text if text is not None else json.dumps(payload)
    message = SimpleNamespace(content=body)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def status_error(cls, status, message="boom"):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    if cls is openai.APIConnectionError:
        return cls(request=request)
    if cls is openai.APITimeoutError:
        return cls(request=request)
    return cls(message, response=response, body={"error": {"message": message}})


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(settings, *outcomes):
    client = FakeClient(*outcomes)
    return deepseek.grade(settings, [b"scheme"], [b"student"], client=client), client


def test_success(settings):
    result, _ = run(settings, completion())
    assert (result.score_awarded, result.score_total, result.topic) == (2, 2, "Algebra")


def test_request_shape(settings):
    _, client = run(settings, completion())
    call = client.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0] == {"role": "system", "content": deepseek.SYSTEM_PROMPT}
    assert "json" in deepseek.SYSTEM_PROMPT.lower()  # DeepSeek requires the word "json" in the prompt


def test_scheme_precedes_student_as_image_url_blocks(settings):
    _, client = run(settings, completion())
    content = client.calls[0]["messages"][1]["content"]
    kinds = ["img" if b["type"] == "image_url" else b["text"][:8] for b in content]
    assert kinds == ["OFFICIAL", "img", "STUDENT'", "img", "Mark the"]
    images = [b for b in content if b["type"] == "image_url"]
    assert all(b["image_url"]["url"].startswith("data:image/jpeg;base64,") for b in images)


def test_client_uses_configured_key_and_base_url(settings):
    client = deepseek.make_client(settings)
    assert client.api_key == "SECRET-KEY-123"
    assert str(client.base_url).startswith("https://proxy.example.com/deepseek")


@pytest.mark.parametrize(
    "exc,expected",
    [
        (status_error(openai.AuthenticationError, 401), "rejected the API key"),
        (status_error(openai.PermissionDeniedError, 403), "not allowed"),
        (status_error(openai.NotFoundError, 404), "DEEPSEEK_MODEL"),
        (status_error(openai.RateLimitError, 429), "rate-limited"),
        (status_error(openai.BadRequestError, 400, "invalid image"), "invalid image"),
        (status_error(openai.InternalServerError, 500, "overloaded"), "temporarily unavailable"),
        (status_error(openai.APITimeoutError, None), "too long"),
        (status_error(openai.APIConnectionError, None), "Could not reach"),
    ],
)
def test_api_errors_become_friendly_messages(settings, exc, expected):
    with pytest.raises(deepseek.GradingError, match=expected):
        run(settings, exc)


def test_error_messages_never_leak_the_key(settings):
    exc = status_error(openai.BadRequestError, 400, "bad request for key SECRET-KEY-123")
    with pytest.raises(deepseek.GradingError) as info:
        run(settings, exc)
    assert "SECRET-KEY-123" not in str(info.value)


def test_api_errors_are_not_retried_by_us(settings):
    client = FakeClient(status_error(openai.RateLimitError, 429))
    with pytest.raises(deepseek.GradingError):
        deepseek.grade(settings, [b"s"], [b"w"], client=client)
    assert len(client.calls) == 1  # the SDK already retries transient errors internally


def test_malformed_json_is_retried_once(settings):
    result, client = run(settings, completion(text="{not json", finish="length"), completion())
    assert result.score_total == 2 and len(client.calls) == 2


def test_persistent_malformed_output_fails_cleanly(settings):
    with pytest.raises(deepseek.GradingError, match="cut off"):
        run(settings, completion(text="{not json", finish="length"), completion(text="{not json", finish="length"))


def test_unusable_marking_is_retried(settings):
    empty = {**GOOD, "marks": []}
    result, _ = run(settings, completion(empty), completion())
    assert result.score_total == 2


def test_empty_answer(settings):
    with pytest.raises(deepseek.GradingError, match="empty answer"):
        run(settings, completion(text=""))


def test_first_attempt_reasons_at_high_effort(settings):
    _, client = run(settings, completion())
    assert client.calls[0]["reasoning_effort"] == "high"
    assert client.calls[0]["max_tokens"] >= 32000


def test_reasoning_exhaustion_retries_at_low_effort(settings):
    result, client = run(settings, completion(text="", finish="length"), completion())
    assert result.score_total == 2
    assert [c["reasoning_effort"] for c in client.calls] == ["high", "low"]


def test_reasoning_exhaustion_twice_fails_cleanly(settings):
    with pytest.raises(deepseek.GradingError, match="ran out of space"):
        run(settings, completion(text="", finish="length"), completion(text="", finish="length"))


def test_markdown_fences_are_not_stripped_but_json_loads_may_still_fail(settings):
    # DeepSeek is instructed not to use fences; if it does anyway, retry should recover
    # once the second attempt returns clean JSON.
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    result, client = run(settings, completion(text=fenced), completion())
    assert result.score_total == 2 and len(client.calls) == 2
