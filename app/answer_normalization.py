"""Normalize LLM chat replies to user-facing prose (no raw JSON in the UI)."""

from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def normalize_llm_answer_to_prose(raw: str | None) -> str:
    """Strip markdown code fences and expand JSON objects into readable text.

    Idempotent for normal paragraphs. Safe to call on cache hits and fresh model output.
    """
    text = (raw or "").strip()
    if not text:
        return text

    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()

    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return _json_object_to_prose(obj).strip()
        except json.JSONDecodeError:
            pass

    return text


def _json_object_to_prose(obj: dict[str, Any]) -> str:
    """Turn a model-generated JSON object into summary + optional References section."""
    skip = frozenset(
        {
            "references",
            "sources",
            "citations",
            "links",
            "articles",
            "metadata",
            "meta",
        }
    )

    body_keys = (
        "summary",
        "answer",
        "response",
        "content",
        "text",
        "analysis",
        "narrative",
        "message",
        "explanation",
        "overview",
    )

    body = ""
    for key in body_keys:
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            body = v.strip()
            break

    if not body:
        parts: list[str] = []
        for k, v in obj.items():
            if k.lower() in skip:
                continue
            if isinstance(v, str) and len(v.strip()) > 20:
                parts.append(v.strip())
            elif isinstance(v, (int, float)) and k.lower() not in ("status", "code"):
                parts.append(f"{k}: {v}")
        body = "\n\n".join(parts) if parts else ""

    refs_lines = _collect_reference_lines(obj)

    out = body.rstrip() if body else ""
    if refs_lines:
        if out:
            out += "\n\n"
        out += "**References**\n" + "\n".join(refs_lines)
    if not out:
        lines: list[str] = []
        for k, v in obj.items():
            if isinstance(v, str) and v.strip():
                lines.append(v.strip())
            elif isinstance(v, (list, dict)):
                continue
            elif v is not None and str(v).strip():
                lines.append(f"{k}: {v}")
        out = "\n\n".join(lines) if lines else ""
    if not out:
        out = "Summary could not be extracted from the model response. Please try rephrasing your question."
    return out


def _collect_reference_lines(obj: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("references", "sources", "citations", "links", "articles"):
        arr = obj.get(key)
        if not isinstance(arr, list):
            continue
        for item in arr:
            if isinstance(item, dict):
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("headline")
                    or item.get("article_title")
                    or "Article"
                )
                link = (
                    item.get("url") or item.get("link") or item.get("source") or item.get("source_url") or ""
                )
                ticker = item.get("ticker") or item.get("symbol") or ""
                if link:
                    line = f"- {title}"
                    if ticker:
                        line += f" ({ticker})"
                    line += f"\n  {link}"
                    lines.append(line)
                elif ticker:
                    lines.append(f"- {title} ({ticker})")
                else:
                    lines.append(f"- {title}")
            elif isinstance(item, str) and item.strip():
                lines.append(f"- {item.strip()}")
    return lines
