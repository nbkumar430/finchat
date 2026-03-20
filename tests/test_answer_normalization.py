"""Tests for LLM answer normalization (no raw JSON in user-facing text)."""

from app.answer_normalization import normalize_llm_answer_to_prose


def test_plain_paragraph_unchanged():
    s = "Apple reported strong services growth.\n\nDetails follow."
    assert normalize_llm_answer_to_prose(s) == s


def test_strips_json_markdown_fence():
    raw = '```json\n{"summary": "Hello world.", "references": []}\n```'
    out = normalize_llm_answer_to_prose(raw)
    assert "Hello world." in out
    assert "```" not in out
    assert '"summary"' not in out


def test_parses_json_object_with_summary_and_sources():
    raw = (
        '{"summary": "NVDA discussed AI demand.", "references": ['
        '{"title": "Chip outlook", "link": "https://example.com/a", "ticker": "NVDA"}]}'
    )
    out = normalize_llm_answer_to_prose(raw)
    assert "NVDA discussed AI demand." in out
    assert "**References**" in out
    assert "Chip outlook" in out
    assert "https://example.com/a" in out
    assert '"summary"' not in out


def test_insufficient_message_preserved():
    msg = "The provided news articles do not contain enough information to answer this question."
    assert normalize_llm_answer_to_prose(msg) == msg
