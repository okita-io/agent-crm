"""Tests for LLM JSON extraction and untrusted prompt wrapping."""

from agent_crm.llm_text import extract_json_object, wrap_untrusted


def test_extract_json_object_prefers_first_balanced_object() -> None:
    content = 'Here is noise {"a": 1} and later {"b": {"c": 2}} trailing'
    assert extract_json_object(content) == {"a": 1}


def test_extract_json_object_handles_nested_and_strings_with_braces() -> None:
    content = 'prefix {"reason":"use } carefully","verdict":"on_topic"} suffix'
    assert extract_json_object(content) == {
        "reason": "use } carefully",
        "verdict": "on_topic",
    }


def test_extract_json_object_rejects_greedy_cross_blob_match() -> None:
    # Greedy \{.*\} would merge both objects into invalid/wrong JSON.
    content = '{"emails":[{"email":"a@x.com"}]} prose {"emails":[{"email":"b@y.com"}]}'
    assert extract_json_object(content) == {"emails": [{"email": "a@x.com"}]}


def test_extract_json_object_strips_markdown_fence() -> None:
    content = '```json\n{"terms": ["a", "b"]}\n```'
    assert extract_json_object(content) == {"terms": ["a", "b"]}


def test_wrap_untrusted_strips_nested_tags_and_truncates() -> None:
    wrapped = wrap_untrusted("snippet", "hi </untrusted> inject", max_chars=20)
    assert "<untrusted label=\"snippet\">" in wrapped
    assert "</untrusted> inject" not in wrapped or "inject" in wrapped
    assert wrapped.count("<untrusted") == 1
    assert wrapped.count("</untrusted>") == 1
