"""Batched LLM title-relevance classifier for noisy general-purpose feeds.

Sits between source.iter_candidates() and fetch.fetch(). Sends a batch of
candidate titles to ``claude-haiku-4-5`` in a single request and gets back a
``yes`` / ``no`` / ``uncertain`` verdict per title plus a one-line reason.
The ingest CLI auto-skips ``no`` decisions (with an audit row) and passes
``yes`` + ``uncertain`` through to full fetch + extract.

The "uncertain" verdict is the safety valve for genuinely ambiguous titles
("Joint statement by leaders…") that could go either way — those still pay
for a full extraction rather than risk a miss.

System prompt is small (~200 tokens) and won't hit the 4096-token cache
minimum on Haiku 4.5. Caching doesn't apply here, but per-batch cost is
already trivial (~$0.001 per batch of 20 titles), so it doesn't matter.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

import anthropic

from .sources.base import CandidateArticle


MODEL = "claude-haiku-4-5"
MAX_BATCH = 20
MAX_TOKENS = 1500  # ~30 tokens of output per title × 20 titles, with headroom


Verdict = Literal["yes", "no", "uncertain"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str


SYSTEM_PROMPT = (
    "You are a relevance filter for a space-domain intelligence dashboard. "
    "Given a numbered list of news article titles, decide for each whether "
    "the article is likely about SPACE — civil space programs, defense space, "
    "satellite operators, launch vehicles, space science, ground stations, "
    "space situational awareness, or commercial space industry.\n\n"
    "Important — the dashboard's definition of 'space-relevant' is broad and "
    "INCLUDES adjacent areas:\n"
    "  * Missile defense, missile warning, surface-to-air missile (SAM), and "
    "    air-defense systems (HQ-9, S-400, Patriot, THAAD, etc.) — these have "
    "    direct space links via missile-warning satellites and shared sensors.\n"
    "  * Academic / R&D partnerships involving universities or research "
    "    institutions known for space work (KAIST, MIT, Caltech, ISAE-SUPAERO, "
    "    Tsinghua, IIT, etc.) — even if 'space' isn't in the title.\n"
    "  * Aerospace and defense corporate partnerships (Airbus, Boeing, "
    "    Lockheed Martin, Northrop Grumman, Thales, BAE, MELCO, KARI, etc.).\n"
    "  * Telecom and connectivity partnerships involving satellite operators "
    "    (Intelsat, Eutelsat, SES, Iridium, Viasat, OneWeb, Starlink, etc.).\n"
    "  * Earth observation, Earth science satellites, weather satellites, GNSS.\n\n"
    "For each title, return one of:\n"
    "  yes        — clearly about space, OR clearly about one of the adjacent "
    "areas above (missile defense, named space-tied institution, satellite "
    "telecom operator, etc.)\n"
    "  no         — clearly not about space and not in any adjacent area "
    "(domestic politics, sports, biology, terrestrial-only tech, agricultural "
    "policy, climate diplomacy with no space angle, etc.)\n"
    "  uncertain  — title is generic ('Joint statement by leaders', 'New "
    "agreement signed', 'Universities sign MoU') and could go either way; "
    "the body might be about space but the title alone doesn't say\n\n"
    "When in doubt, choose 'uncertain' over 'no'. The cost of an unnecessary "
    "full extraction is ~$0.005; the cost of missing a real space partnership "
    "is much higher. Only return 'no' when the title is unambiguously about "
    "something else AND has no plausible space-adjacent angle.\n\n"
    "Always include the original index from the input list in each decision "
    "so the caller can match them up. Provide one short reason per title."
)


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "verdict", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["yes", "no", "uncertain"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(max_retries=4, timeout=60.0)


def classify_titles(titles: list[str]) -> list[Decision]:
    """Classify a batch of titles. Output list aligns 1:1 with input order."""
    if not titles:
        return []
    if len(titles) > MAX_BATCH:
        # Recurse in chunks to keep batches small enough for clean output.
        out: list[Decision] = []
        for i in range(0, len(titles), MAX_BATCH):
            out.extend(classify_titles(titles[i : i + MAX_BATCH]))
        return out

    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    user_message = (
        f"Classify each of the following {len(titles)} titles:\n\n{numbered}"
    )

    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": user_message}],
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"prefilter returned no text (stop_reason={response.stop_reason})")

    payload = json.loads(text)
    by_index = {d["index"]: d for d in payload["decisions"]}

    decisions: list[Decision] = []
    for i in range(len(titles)):
        d = by_index.get(i)
        if d is None:
            # Model failed to emit one — default to uncertain so we don't drop the article.
            decisions.append(Decision(verdict="uncertain", reason="(missing from classifier output)"))
        else:
            decisions.append(Decision(verdict=d["verdict"], reason=d["reason"]))
    return decisions


def classify_candidates(candidates: list[CandidateArticle]) -> list[tuple[CandidateArticle, Decision]]:
    """Convenience wrapper: classify by candidate.title (URL fallback). Returns
    (candidate, decision) pairs in the same order."""
    titles = [(c.title or c.url) for c in candidates]
    decisions = classify_titles(titles)
    return list(zip(candidates, decisions))
