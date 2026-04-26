"""
Fetches deals via Serper.dev Google Search API.
Free tier: 2500 searches/month.
Sign up at https://serper.dev to get your API key.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from config import MIN_DISCOUNT_PERCENT, SEARCH_QUERIES

logger = logging.getLogger(__name__)

SERPER_API_URL = "https://google.serper.dev/shopping"
SERPER_SEARCH_URL = "https://google.serper.dev/search"


def _get_api_key() -> Optional[str]:
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        logger.warning(
            "SERPER_API_KEY not set — skipping Serper searches. "
            "Add it to GitHub repo secrets: Settings → Secrets → Actions → SERPER_API_KEY"
        )
    else:
        logger.info(f"SERPER_API_KEY loaded (length: {len(key)}, starts: {key[:4]}...)")
    return key


def _extract_discount_from_text(text: str) -> Optional[float]:
    """Extract discount percentage from product title or snippet."""
    match = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    if re.search(r"half[\s-]?price", text, re.IGNORECASE):
        return 50.0
    return None


def _parse_price(price_str: str) -> Optional[float]:
    """Parse a price string like '$1,299.00' into a float."""
    if not price_str:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _search_shopping(query: str, api_key: str) -> list[dict]:
    """Run a Google Shopping search via Serper."""
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "gl": "au",          # Australia
        "hl": "en",
        "num": 10,
    }

    try:
        response = requests.post(SERPER_API_URL, json=payload, headers=headers, timeout=15)
        if response.status_code == 403:
            logger.error(
                f"Serper 403 Forbidden for '{query}'. "
                f"Check your SERPER_API_KEY is correct and active at serper.dev. "
                f"Response: {response.text[:200]}"
            )
            return []
        response.raise_for_status()
        data = response.json()
        return data.get("shopping", [])
    except requests.RequestException as e:
        logger.error(f"Serper shopping search failed for '{query}': {e}")
        return []


def _search_web(query: str, api_key: str) -> list[dict]:
    """Run a Google web search via Serper for deal pages."""
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query + " site:ozbargain.com.au OR site:jbhifi.com.au OR site:kogan.com OR site:catch.com.au",
        "gl": "au",
        "hl": "en",
        "num": 10,
    }

    try:
        response = requests.post(SERPER_SEARCH_URL, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("organic", [])
    except requests.RequestException as e:
        logger.error(f"Serper web search failed for '{query}': {e}")
        return []


def fetch_serper_deals() -> list[dict]:
    """
    Run configured search queries via Serper and return filtered deals.
    """
    api_key = _get_api_key()
    if not api_key:
        return []

    deals = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        logger.info(f"Serper shopping search: '{query}'")
        results = _search_shopping(query, api_key)

        for item in results:
            title = item.get("title", "")
            link = item.get("link", "")
            price_str = item.get("price", "")
            original_price_str = item.get("originalPrice", "")
            source = item.get("source", "")
            rating = item.get("rating")
            reviews = item.get("ratingCount")

            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            sale_price = _parse_price(price_str)
            original_price = _parse_price(original_price_str)

            # Calculate discount
            discount_pct = None
            if original_price and sale_price and original_price > 0:
                discount_pct = round((1 - sale_price / original_price) * 100, 1)

            # Also check title/snippet for discount mentions
            if discount_pct is None:
                discount_pct = _extract_discount_from_text(title)

            # Filter: must have a meaningful discount
            if discount_pct is None or discount_pct < MIN_DISCOUNT_PERCENT:
                continue

            deal = {
                "id": f"serper_{abs(hash(link))}",
                "source": f"serper_shopping_{source}",
                "title": title,
                "url": link,
                "description": f"{title} — {source}. Price: {price_str}",
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": 0,
                "rating": rating,
                "reviews": reviews,
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            deals.append(deal)

    logger.info(f"Serper: {len(deals)} deals found across {len(SEARCH_QUERIES)} queries")
    return deals
