"""On-demand article translation via Claude.

Single uncached call: take article body in any language, return English. Used
by the article-review view when an analyst clicks "Translate to English" on
a non-English article.

Cost is ~$0.005 per typical article body. Caller is responsible for caching
(the Streamlit UI uses ``st.session_state`` keyed by article id).
"""

from __future__ import annotations

import os

import anthropic


MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4000


SYSTEM_PROMPT = (
    "You translate news articles to English. Preserve paragraph structure, "
    "named entities, and technical terms. Do not add commentary, headers, "
    "or notes about the translation — return only the translated text. If "
    "the input is already in English, return it unchanged."
)


def translate_to_english(text: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Translation requires the same key "
            "the extractor uses."
        )
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    out = next((b.text for b in response.content if b.type == "text"), None)
    if out is None:
        raise RuntimeError(f"Translation returned no text (stop_reason={response.stop_reason})")
    return out
