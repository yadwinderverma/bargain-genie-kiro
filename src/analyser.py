"""
LLM-based deal analyser using Google Gemini SDK (google-genai).
Uses structured output (response_schema) so we never need to parse JSON manually.

Free tier: 15 req/min, 1500 req/day on gemini-2.5-flash.
Get your API key at: https://aistudio.google.com/app/apikey
"""

import logging
import os
import time
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel

from config import LLM_MAX_DEALS_PER_BATCH, LLM_MIN_SCORE, LLM_MODEL, OZBARGAIN_SCORE_BOOST, OZBARGAIN_TRUSTED

logger = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 4  # Seconds between batches — free tier is 15 req/min


# ---------------------------------------------------------------------------
# Structured output schema — Gemini will return exactly this shape
# ---------------------------------------------------------------------------

class DealScore(BaseModel):
    deal_index: int
    score: int                  # 1–10
    genuine_discount: bool
    reason: str                 # Max ~20 words
    category: str               # e.g. "Electronics", "Appliances"


class DealAnalysis(BaseModel):
    results: list[DealScore]


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def _get_client() -> Optional[genai.Client]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning(
            "GEMINI_API_KEY not set — skipping LLM analysis, passing all deals through. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
        return None
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(deals: list[dict]) -> str:
    deals_text = ""
    for i, deal in enumerate(deals, 1):
        community_note = ""
        if deal.get("source") == "ozbargain" and deal.get("community_validated"):
            community_note = f" [COMMUNITY VALIDATED — {deal.get('votes', 0)} OzBargain upvotes]"
        elif deal.get("price_beat_retailer"):
            community_note = " [OFFICEWORKS — 5% Price Beat Guarantee, likely lowest AU price]"

        deals_text += (
            f"\nDeal {i}:{community_note}\n"
            f"  Title:          {deal.get('title', 'Unknown')}\n"
            f"  Source:         {deal.get('source', 'Unknown')}\n"
            f"  Original Price: ${deal.get('original_price') or 'Unknown'}\n"
            f"  Sale Price:     ${deal.get('sale_price') or 'Unknown'}\n"
            f"  Discount:       {deal.get('discount_pct') or 'Unknown'}%\n"
            f"  OzBargain Votes:{deal.get('votes', 0)}\n"
            f"  Description:    {deal.get('description', '')[:200]}\n"
        )

    return (
        "You are an expert Australian bargain hunter. Rate each deal below.\n\n"
        "SIGNALS (in order of trust):\n"
        "1. OzBargain COMMUNITY VALIDATED — the Australian deal community posted and upvoted it. "
        "That alone is a strong signal. Score baseline 7+. Only go lower if it's clearly a fake "
        "discount or a product nobody would want.\n"
        "2. Officeworks PRICE BEAT — they guarantee to beat any AU competitor by 5%, so if "
        "they're the cheapest it's the best available price in Australia. Score on value.\n"
        "3. Amazon AU / other retailers — only included if 40%+ off market price. "
        "Verify the discount looks real (not an inflated original price trick). Score 7+ "
        "only if you'd genuinely tell a friend about it.\n\n"
        "REJECT if:\n"
        "- Original price looks inflated to manufacture a fake % off\n"
        "- It's a used/refurbished item not clearly disclosed\n"
        "- The 'deal' is just normal retail price\n\n"
        f"{deals_text}\n"
        "Score: 1–4 skip, 5–6 marginal, 7–8 good deal, 9–10 exceptional.\n"
        "OzBargain community pick → baseline 7.\n"
        "Retailer 40%+ off → 7 if discount is genuine, higher if exceptional value."
    )


# ---------------------------------------------------------------------------
# Score attachment + OzBargain boost
# ---------------------------------------------------------------------------

def _attach_scores(deals: list[dict], results: list[DealScore]) -> list[dict]:
    score_map = {r.deal_index: r for r in results}

    for i, deal in enumerate(deals, 1):
        result = score_map.get(i)
        if result is None:
            logger.warning(f"No score returned for deal {i}: {deal.get('title', '')[:50]}")
            deal["llm_score"] = LLM_MIN_SCORE
            deal["llm_reason"] = "No LLM score returned"
            deal["llm_category"] = "General"
            deal["llm_genuine"] = True
            continue

        base_score = max(1, min(10, result.score))  # Clamp to 1–10

        # OzBargain community trust boost
        if (
            OZBARGAIN_TRUSTED
            and deal.get("source") == "ozbargain"
            and deal.get("community_validated")
        ):
            boosted = min(10, base_score + OZBARGAIN_SCORE_BOOST)
            if boosted != base_score:
                logger.info(
                    f"OzBargain boost: '{deal.get('title', '')[:45]}' "
                    f"{base_score} → {boosted}"
                )
            deal["llm_score"] = boosted
        else:
            deal["llm_score"] = base_score

        deal["llm_reason"] = result.reason
        deal["llm_category"] = result.category
        deal["llm_genuine"] = result.genuine_discount

    return deals


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyse_deals(deals: list[dict]) -> list[dict]:
    """
    Score deals with Gemini and return only those >= LLM_MIN_SCORE.
    Uses structured output — no JSON parsing needed.
    """
    client = _get_client()

    if not client:
        # No API key — pass everything through
        for deal in deals:
            deal["llm_score"] = 7
            deal["llm_reason"] = "LLM skipped (no API key)"
            deal["llm_category"] = "General"
            deal["llm_genuine"] = True
        return deals

    if not deals:
        return []

    logger.info(f"Analysing {len(deals)} deals with {LLM_MODEL}")
    scored_deals = []

    for i in range(0, len(deals), LLM_MAX_DEALS_PER_BATCH):
        batch = deals[i : i + LLM_MAX_DEALS_PER_BATCH]
        batch_num = i // LLM_MAX_DEALS_PER_BATCH + 1
        logger.info(f"LLM batch {batch_num}/{-(-len(deals) // LLM_MAX_DEALS_PER_BATCH)}: {len(batch)} deals")

        prompt = _build_prompt(batch)

        try:
            response = client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DealAnalysis,
                    temperature=0.1,
                ),
            )
            analysis: DealAnalysis = response.parsed
            batch = _attach_scores(batch, analysis.results)
            logger.info(
                f"Batch {batch_num} scores: "
                + ", ".join(f"{d.get('llm_score')}" for d in batch)
            )

        except Exception as e:
            logger.error(f"Gemini call failed for batch {batch_num}: {e}")
            # On failure, pass deals through at threshold so they aren't silently dropped
            for deal in batch:
                deal["llm_score"] = LLM_MIN_SCORE
                deal["llm_reason"] = f"LLM error — unfiltered ({type(e).__name__})"
                deal["llm_category"] = "General"
                deal["llm_genuine"] = True

        scored_deals.extend(batch)

        # Respect free tier rate limit between batches
        if i + LLM_MAX_DEALS_PER_BATCH < len(deals):
            time.sleep(RATE_LIMIT_DELAY)

    passing = [d for d in scored_deals if d.get("llm_score", 0) >= LLM_MIN_SCORE]
    logger.info(
        f"LLM filter: {len(scored_deals)} analysed → {len(passing)} passed (score >= {LLM_MIN_SCORE})"
    )
    return passing
