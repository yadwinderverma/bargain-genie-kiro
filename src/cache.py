"""
Deal cache — stored as a JSON file committed to the repo.
Prevents duplicate alerts across runs.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from config import CACHE_FILE, CACHE_MAX_AGE_DAYS

logger = logging.getLogger(__name__)


def _load_cache() -> dict:
    """Load the cache from disk. Returns empty dict if not found."""
    if not os.path.exists(CACHE_FILE):
        logger.info(f"Cache file not found at {CACHE_FILE}, starting fresh")
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Could not read cache file: {e}. Starting fresh.")
        return {}


def _save_cache(cache: dict) -> None:
    """Save the cache to disk."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        logger.info(f"Cache saved: {len(cache)} entries")
    except IOError as e:
        logger.error(f"Failed to save cache: {e}")


def _purge_old_entries(cache: dict) -> dict:
    """Remove entries older than CACHE_MAX_AGE_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=CACHE_MAX_AGE_DAYS)
    before = len(cache)
    cache = {
        deal_id: entry
        for deal_id, entry in cache.items()
        if datetime.fromisoformat(entry["seen_at"]) > cutoff
    }
    removed = before - len(cache)
    if removed:
        logger.info(f"Purged {removed} old cache entries")
    return cache


def filter_new_deals(deals: list[dict]) -> list[dict]:
    """
    Given a list of deals, return only those not already in the cache.
    Also updates the cache with newly seen deals.
    """
    cache = _load_cache()
    cache = _purge_old_entries(cache)

    new_deals = []
    now = datetime.now(timezone.utc).isoformat()

    for deal in deals:
        deal_id = deal.get("id", "")
        if not deal_id:
            continue
        if deal_id not in cache:
            new_deals.append(deal)
            cache[deal_id] = {
                "seen_at": now,
                "title": deal.get("title", ""),
                "source": deal.get("source", ""),
            }

    logger.info(f"Cache filter: {len(deals)} deals in → {len(new_deals)} new deals")
    _save_cache(cache)
    return new_deals


def mark_deals_alerted(deals: list[dict]) -> None:
    """
    Mark deals as alerted in the cache (adds 'alerted' flag).
    Call this after successfully sending Slack notifications.
    """
    cache = _load_cache()
    for deal in deals:
        deal_id = deal.get("id", "")
        if deal_id in cache:
            cache[deal_id]["alerted"] = True
    _save_cache(cache)
