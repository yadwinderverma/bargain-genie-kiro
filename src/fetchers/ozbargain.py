"""
Fetches deals from OzBargain RSS feed.
No API key required — OzBargain provides a public RSS feed.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests

from config import (
    MIN_DISCOUNT_PERCENT, MIN_OZBARGAIN_VOTES, OZBARGAIN_MAX_ITEMS,
    OZBARGAIN_RSS_URL, OZBARGAIN_TRUSTED, OZBARGAIN_MIN_VOTES_TRUSTED,
    SEARCH_QUERIES, OZBARGAIN_FREEBIES_ENABLED, OZBARGAIN_FREEBIES_RSS_URL,
    OZBARGAIN_FREEBIES_MAX_ITEMS, OZBARGAIN_FREEBIES_MIN_VOTES,
)

logger = logging.getLogger(__name__)


def _parse_discount_from_title(title: str) -> Optional[float]:
    """Try to extract a discount percentage from the deal title."""
    # Match patterns like "50% off", "50%off", "50 % off"
    match = re.search(r"(\d+)\s*%\s*off", title, re.IGNORECASE)
    if match:
        return float(match.group(1))
    # Match patterns like "half price", "half-price"
    if re.search(r"half[\s-]?price", title, re.IGNORECASE):
        return 50.0
    return None


def _parse_price_from_description(description: str) -> tuple[Optional[float], Optional[float]]:
    """
    Try to extract original and sale prices from the deal description HTML.
    Returns (original_price, sale_price).
    """
    # Look for price patterns like $99.99 or $1,299
    prices = re.findall(r"\$[\d,]+(?:\.\d{2})?", description)
    prices_clean = []
    for p in prices:
        try:
            prices_clean.append(float(p.replace("$", "").replace(",", "")))
        except ValueError:
            pass

    if len(prices_clean) >= 2:
        # Assume higher price is original, lower is sale
        original = max(prices_clean[:4])  # Look at first 4 price mentions
        sale = min(prices_clean[:4])
        if original > sale:
            return original, sale
    elif len(prices_clean) == 1:
        return None, prices_clean[0]

    return None, None


def _parse_votes(entry) -> int:
    """Extract vote count from OzBargain RSS entry tags."""
    # OzBargain includes vote info in tags or summary
    # Try to find it in the description
    description = entry.get("summary", "")
    vote_match = re.search(r"(\d+)\s*(?:votes?|clicks?)", description, re.IGNORECASE)
    if vote_match:
        return int(vote_match.group(1))
    # Fallback: check tags
    tags = entry.get("tags", [])
    for tag in tags:
        if "vote" in tag.get("term", "").lower():
            try:
                return int(re.search(r"\d+", tag["term"]).group())
            except (AttributeError, ValueError):
                pass
    return 0


def _matches_search_queries(title: str, description: str) -> bool:
    """
    Check if a deal title/description matches any of the user's search queries.
    Each query is split into keywords — all keywords must appear (case-insensitive).
    e.g. "beats powerbeats pro 2" requires all four words to be present.
    """
    text = (title + " " + description).lower()
    for query in SEARCH_QUERIES:
        keywords = query.lower().split()
        if all(kw in text for kw in keywords):
            return True
    return False


def fetch_ozbargain_deals() -> list[dict]:
    """
    Fetch and parse deals from OzBargain RSS feed.
    Returns a list of deal dicts.
    """
    logger.info(f"Fetching OzBargain RSS feed: {OZBARGAIN_RSS_URL}")
    deals = []

    try:
        feed = feedparser.parse(OZBARGAIN_RSS_URL)
        if feed.bozo and not feed.entries:
            logger.error(f"Failed to parse OzBargain RSS: {feed.bozo_exception}")
            return deals

        logger.info(f"Found {len(feed.entries)} entries in OzBargain feed")

        for entry in feed.entries[:OZBARGAIN_MAX_ITEMS]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            description = entry.get("summary", "")
            published = entry.get("published", "")

            # Try to extract discount
            discount_pct = _parse_discount_from_title(title)
            original_price, sale_price = _parse_price_from_description(description)

            # Calculate discount from prices if not in title
            if discount_pct is None and original_price and sale_price and original_price > 0:
                discount_pct = round((1 - sale_price / original_price) * 100, 1)

            votes = _parse_votes(entry)

            # --- Product filter: only keep deals matching your SEARCH_QUERIES ---
            if not _matches_search_queries(title, description):
                continue

            # Apply filters
            passes_discount = discount_pct is not None and discount_pct >= MIN_DISCOUNT_PERCENT
            passes_votes = votes >= MIN_OZBARGAIN_VOTES

            # OzBargain trust logic:
            # If OZBARGAIN_TRUSTED is on, anything that made it onto OzBargain with
            # even a handful of votes is worth considering — the community already
            # filtered out the junk. We don't require a 50% discount here.
            if OZBARGAIN_TRUSTED:
                community_validated = votes >= OZBARGAIN_MIN_VOTES_TRUSTED
                passes = passes_discount or passes_votes or community_validated
            else:
                passes = passes_discount or passes_votes

            if not passes:
                continue

            deal = {
                "id": f"ozb_{entry.get('id', link)}",
                "source": "ozbargain",
                "title": title,
                "url": link,
                "description": description[:500],  # Truncate for LLM
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": votes,
                "community_validated": OZBARGAIN_TRUSTED and votes >= OZBARGAIN_MIN_VOTES_TRUSTED,
                "published": published,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            deals.append(deal)

        logger.info(f"OzBargain: {len(deals)} deals passed initial filters")

    except Exception as e:
        logger.error(f"Error fetching OzBargain deals: {e}", exc_info=True)

    return deals


def fetch_ozbargain_freebies() -> list[dict]:
    """
    Fetch freebies from OzBargain's dedicated freebie RSS feed.
    No product filter — everything free with enough votes is worth alerting.
    Free trials, free subscriptions, free apps, free products, etc.
    """
    if not OZBARGAIN_FREEBIES_ENABLED:
        return []

    logger.info(f"Fetching OzBargain freebies feed: {OZBARGAIN_FREEBIES_RSS_URL}")
    freebies = []

    try:
        feed = feedparser.parse(OZBARGAIN_FREEBIES_RSS_URL)
        if feed.bozo and not feed.entries:
            logger.error(f"Failed to parse OzBargain freebies RSS: {feed.bozo_exception}")
            return freebies

        logger.info(f"Found {len(feed.entries)} freebie entries")

        for entry in feed.entries[:OZBARGAIN_FREEBIES_MAX_ITEMS]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            description = entry.get("summary", "")
            published = entry.get("published", "")
            votes = _parse_votes(entry)

            # Only surface well-upvoted freebies — filters out low-quality spam
            if votes < OZBARGAIN_FREEBIES_MIN_VOTES:
                logger.debug(f"Freebie skipped (only {votes} votes): {title[:60]}")
                continue

            # Detect if it's time-limited (e.g. "2 months free", "free trial")
            is_limited = bool(re.search(
                r"\d+\s*(day|week|month|year)s?\s*free|free\s*trial|limited\s*time",
                title + " " + description,
                re.IGNORECASE,
            ))
            is_lifetime = bool(re.search(
                r"lifetime|forever|permanent|always\s*free",
                title + " " + description,
                re.IGNORECASE,
            ))

            duration_note = ""
            if is_lifetime:
                duration_note = "lifetime"
            elif is_limited:
                duration_note = "limited time"

            freebies.append({
                "id": f"ozb_free_{entry.get('id', link)}",
                "source": "ozbargain_freebie",
                "title": title,
                "url": link,
                "description": description[:500],
                "original_price": None,
                "sale_price": 0.0,
                "discount_pct": 100.0,
                "votes": votes,
                "community_validated": True,  # Freebies are always community-validated
                "is_freebie": True,
                "duration_note": duration_note,
                "published": published,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

        logger.info(f"OzBargain freebies: {len(freebies)} passed (>= {OZBARGAIN_FREEBIES_MIN_VOTES} votes)")

    except Exception as e:
        logger.error(f"Error fetching OzBargain freebies: {e}", exc_info=True)

    return freebies
