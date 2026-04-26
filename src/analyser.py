"""
LLM-based deal analyser using Google Gemini API (free tier).
Scores deals 1-10 for genuine bargain quality and filters out fake discounts.

Free tier: 15 requests/minute, 1500 requests/day.
Get your API key at: https://aistudio.google.com/app/apikey
"""

import json
import logging
import os
import time
from typing import Optional

import requests

from config import LLM_MAX_DEALS_PER_BATCH, LLM_MIN_SCORE, LLM_MODEL, OZBARGAIN_SCORE_BOOST, OZBARGAIN_TRUSTED

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
RATE_LIMIT_DELAY = 5  # Seconds between API calls to stay within free tier


def _get_api_key() -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.warning("GEMINI_API_KEY not set — skipping LLM analysis, passing all deals through")
    return key


def _build_analysis_prompt(deals: list[dict]) -> str:
    """Build the prompt for batch deal analysis."""
    deals_text = ""
    for i, deal in enumerate(deals, 1):
        is_ozb = deal.get("source") == "ozbargain"
        community_note = ""
        if is_ozb and deal.get("community_validated"):
            community_note = f" COMMUNITY VALIDATED on OzBargain ({deal.get('votes', 0)} upvotes)"

        deals_text += (
            f"\nDeal {i}:{community_note}\n"
            f"- Title: {deal.get('title', 'Unknown')}\n"
            f"- Source: {deal.get('source', 'Unknown')}\n"
            f"- Original Price: ${deal.get('original_price') or 'Unknown'}\n"
            f"- Sale Price: ${deal.get('sale_price') or 'Unknown'}\n"
            f"- Discount: {deal.get('discount_pct') or 'Unknown'}%\n"
            f"- OzBargain Votes: {deal.get('votes', 0)}\n"
            f"- Description: {deal.get('description', '')[:200]}\n"
        )

    prompt = (
        "You are an Australian bargain hunting expert. Analyse these deals and rate each one.\n\n"
        "IMPORTANT CONTEXT: OzBargain (ozbargain.com.au) is Australia's largest deal-sharing community. "
        "When a deal is marked 'COMMUNITY VALIDATED on OzBargain', it means real Australian shoppers have "
        "upvoted it as a genuine bargain. These deals have already passed community scrutiny — treat them "
        "with higher confidence. The discount may not always be 50%+ but the community has judged it "
        "worthwhile. Score these more generously unless there is a clear red flag.\n\n"
        "For each deal, assess:\n"
        "1. Is the discount genuine? (Watch for inflated 'original' prices — a common retail trick)\n"
        "2. Is this a good product at a good price for Australian consumers?\n"
        "3. Is the source reputable (OzBargain, JB Hi-Fi, Kogan, Catch, etc.)?\n"
        "4. Would a savvy Australian shopper consider this a real bargain?\n\n"
        f"{deals_text}\n"
        "Respond with ONLY a JSON array (no markdown, no explanation outside JSON) like this:\n"
        "[\n"
        "  {\n"
        '    "deal_index": 1,\n'
        '    "score": 8,\n'
        '    "genuine_discount": true,\n'
        '    "reason": "Brief reason (max 20 words)",\n'
        '    "category": "Electronics"\n'
        "  },\n"
        "  ...\n"
        "]\n\n"
        "Score guide: 1-4 = skip, 5-6 = marginal, 7-8 = good deal, 9-10 = exceptional bargain.\n"
        "For community-validated OzBargain deals, start from a baseline of 7 unless there is a red flag.\n"
        "For other sources, be strict — only score 7+ if you would genuinely recommend it to a friend."
    )

    return prompt


def _call_gemini(prompt: str, api_key: str) -> Optional[str]:
    """Call Gemini API and return the text response."""
    url = f"{GEMINI_API_BASE}/{LLM_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
    except requests.RequestException as e:
        logger.error(f"Gemini API call failed: {e}")
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected Gemini response format: {e}")

    return None


def _parse_llm_response(response_text: str, deals: list[dict]) -> list[dict]:
    """Parse LLM JSON response and attach scores to deals."""
    # Strip markdown code fences if present
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        scores = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}\nResponse: {text[:500]}")
        # Fallback: return all deals with neutral score
        for deal in deals:
            deal["llm_score"] = 5
            deal["llm_reason"] = "LLM parse error — manual review needed"
            deal["llm_category"] = "Unknown"
        return deals

    # Map scores back to deals
    score_map = {item["deal_index"]: item for item in scores if isinstance(item, dict)}

    for i, deal in enumerate(deals, 1):
        score_data = score_map.get(i, {})
        base_score = score_data.get("score", 5)

        # Apply OzBargain trust boost — community-validated deals get a bump
        # since the community has already done the vetting work
        if (
            OZBARGAIN_TRUSTED
            and deal.get("source") == "ozbargain"
            and deal.get("community_validated")
        ):
            boosted_score = min(10, base_score + OZBARGAIN_SCORE_BOOST)
            if boosted_score != base_score:
                logger.debug(
                    f"OzBargain trust boost: '{deal.get('title', '')[:40]}' "
                    f"{base_score} → {boosted_score}"
                )
            deal["llm_score"] = boosted_score
        else:
            deal["llm_score"] = base_score

        deal["llm_reason"] = score_data.get("reason", "No analysis available")
        deal["llm_category"] = score_data.get("category", "General")
        deal["llm_genuine"] = score_data.get("genuine_discount", True)

    return deals


def analyse_deals(deals: list[dict]) -> list[dict]:
    """
    Run LLM analysis on deals and return only those scoring >= LLM_MIN_SCORE.
    Processes deals in batches to stay within API rate limits.
    """
    api_key = _get_api_key()

    if not api_key:
        # No API key — pass all deals through with a default score
        logger.info("No Gemini API key — passing all deals through without LLM scoring")
        for deal in deals:
            deal["llm_score"] = 7
            deal["llm_reason"] = "LLM analysis skipped (no API key)"
            deal["llm_category"] = "General"
            deal["llm_genuine"] = True
        return deals

    if not deals:
        return []

    logger.info(f"Analysing {len(deals)} deals with Gemini ({LLM_MODEL})")
    scored_deals = []

    # Process in batches
    for i in range(0, len(deals), LLM_MAX_DEALS_PER_BATCH):
        batch = deals[i : i + LLM_MAX_DEALS_PER_BATCH]
        batch_num = i // LLM_MAX_DEALS_PER_BATCH + 1
        logger.info(f"LLM batch {batch_num}: analysing {len(batch)} deals")

        prompt = _build_analysis_prompt(batch)
        response_text = _call_gemini(prompt, api_key)

        if response_text:
            batch = _parse_llm_response(response_text, batch)
        else:
            logger.warning(f"No LLM response for batch {batch_num}, using default scores")
            for deal in batch:
                deal["llm_score"] = 5
                deal["llm_reason"] = "LLM unavailable"
                deal["llm_category"] = "General"
                deal["llm_genuine"] = True

        scored_deals.extend(batch)

        # Rate limit: wait between batches
        if i + LLM_MAX_DEALS_PER_BATCH < len(deals):
            time.sleep(RATE_LIMIT_DELAY)

    # Filter by minimum score
    passing = [d for d in scored_deals if d.get("llm_score", 0) >= LLM_MIN_SCORE]
    logger.info(f"LLM filter: {len(scored_deals)} analysed → {len(passing)} passed (score >= {LLM_MIN_SCORE})")

    return passing
