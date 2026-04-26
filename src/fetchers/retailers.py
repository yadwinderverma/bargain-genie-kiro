"""
Retailer price fetcher — uses Serper Shopping API for structured price data.

Strategy:
1. For each product in SEARCH_QUERIES, run a Google Shopping search
2. Collect prices from ALL retailers in one shot (Shopping API returns many retailers)
3. Find the market high price and flag anything MIN_DISCOUNT_PERCENT% below it
4. Officeworks gets special treatment: if they're the cheapest (or close to it),
   flag it as a price-beat deal even without a big % discount
"""

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config import MIN_DISCOUNT_PERCENT, SEARCH_QUERIES, SERPER_ENABLED

logger = logging.getLogger(__name__)

SERPER_SHOPPING_URL = "https://google.serper.dev/shopping"

# Retailers we care about — maps domain keywords to clean names
KNOWN_RETAILERS = {
    "jbhifi.com.au":      "JB Hi-Fi",
    "kogan.com":          "Kogan",
    "catch.com.au":       "Catch",
    "amazon.com.au":      "Amazon AU",
    "bigw.com.au":        "Big W",
    "target.com.au":      "Target AU",
    "officeworks.com.au": "Officeworks",
    "harvey norman":      "Harvey Norman",
    "harveynorman.com.au":"Harvey Norman",
    "myer.com.au":        "Myer",
    "ebay.com.au":        "eBay AU",
    "costco.com.au":      "Costco AU",
}


def _get_api_key() -> Optional[str]:
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        logger.warning("SERPER_API_KEY not set — skipping retailer price search")
    return key


def _parse_price(price_str: str) -> Optional[float]:
    if not price_str:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(price_str).replace(",", ""))
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def _retailer_name_from_source(source: str) -> str:
    """Map a Serper 'source' field to a clean retailer name."""
    source_lower = source.lower()
    for domain, name in KNOWN_RETAILERS.items():
        if domain in source_lower:
            return name
    return source.title()


def _is_officeworks(source: str) -> bool:
    return "officeworks" in source.lower()


def _fetch_shopping_results(query: str, api_key: str) -> list[dict]:
    """
    Run a Google Shopping search and return raw results.
    Shopping API returns structured price + originalPrice per listing.
    """
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": f"{query} Australia",
        "gl": "au",
        "hl": "en",
        "num": 20,  # Get more results to have a good price spread
    }
    try:
        response = requests.post(SERPER_SHOPPING_URL, json=payload, headers=headers, timeout=15)
        if response.status_code == 403:
            logger.error(
                f"Serper 403 — check SERPER_API_KEY is valid at serper.dev. "
                f"Response: {response.text[:200]}"
            )
            return []
        response.raise_for_status()
        return response.json().get("shopping", [])
    except requests.RequestException as e:
        logger.error(f"Serper Shopping search failed for '{query}': {e}")
        return []


def _analyse_prices(query: str, results: list[dict]) -> list[dict]:
    """
    Given Shopping results for a product, find the market price range and
    return deals that are genuinely discounted vs the market.

    Also flags Officeworks if they're the cheapest (price-beat signal).
    """
    if not results:
        return []

    # Parse all prices
    priced = []
    for item in results:
        source = item.get("source", "")
        title = item.get("title", "")
        link = item.get("link", "")
        price_str = item.get("price", "")
        original_str = item.get("originalPrice", "")
        rating = item.get("rating")
        reviews = item.get("ratingCount")

        current_price = _parse_price(price_str)
        original_price = _parse_price(original_str)

        if not current_price or not link:
            continue

        priced.append({
            "source": source,
            "retailer": _retailer_name_from_source(source),
            "title": title,
            "link": link,
            "current_price": current_price,
            "original_price": original_price,
            "rating": rating,
            "reviews": reviews,
            "is_officeworks": _is_officeworks(source),
        })

    if not priced:
        logger.info(f"No priced results for '{query}'")
        return []

    prices = [p["current_price"] for p in priced]
    market_high = max(prices)
    market_low = min(prices)
    market_median = sorted(prices)[len(prices) // 2]

    logger.info(
        f"'{query}' — {len(priced)} retailers: "
        f"low=${market_low:.0f}, median=${market_median:.0f}, high=${market_high:.0f}"
    )

    deals = []
    now = datetime.now(timezone.utc).isoformat()

    for item in priced:
        current_price = item["current_price"]
        original_price = item["original_price"]
        is_officeworks = item["is_officeworks"]

        # Calculate discount vs the item's own original price (if available)
        own_discount = None
        if original_price and original_price > current_price:
            own_discount = round((1 - current_price / original_price) * 100, 1)

        # Calculate discount vs market high (how much cheaper than the most expensive retailer)
        vs_market_high = round((1 - current_price / market_high) * 100, 1) if market_high > 0 else 0

        # Calculate discount vs market median (fairer comparison)
        vs_market_median = round((1 - current_price / market_median) * 100, 1) if market_median > 0 else 0

        # Is this the cheapest option?
        is_cheapest = current_price == market_low
        is_near_cheapest = current_price <= market_low * 1.05  # Within 5% of cheapest

        # --- Decision logic ---
        should_include = False
        deal_reason = ""

        if is_officeworks:
            # Officeworks: include if they're cheapest or near-cheapest
            # Their price beat means this is likely the best you'll get in AU
            if is_cheapest:
                should_include = True
                deal_reason = f"Officeworks cheapest at ${current_price:.0f} (market low, price-beat guarantee)"
            elif is_near_cheapest:
                should_include = True
                deal_reason = f"Officeworks near-cheapest at ${current_price:.0f} (within 5% of market low)"
            elif vs_market_median >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"Officeworks {vs_market_median:.0f}% below median market price"
        else:
            # Other retailers: need a meaningful discount vs market or own original price
            if own_discount is not None and own_discount >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"{own_discount:.0f}% off original price (${original_price:.0f} → ${current_price:.0f})"
            elif vs_market_median >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"{vs_market_median:.0f}% below median market price of ${market_median:.0f}"

        if not should_include:
            logger.debug(
                f"  Skip {item['retailer']}: ${current_price:.0f} "
                f"(own_discount={own_discount}, vs_median={vs_market_median:.0f}%)"
            )
            continue

        # Use the best available discount figure for display
        display_discount = own_discount or (vs_market_median if vs_market_median > 0 else None)

        deals.append({
            "id": f"retail_{abs(hash(item['link']))}",
            "source": item["retailer"].lower().replace(" ", "_"),
            "title": item["title"],
            "url": item["link"],
            "description": (
                f"{item['title']} at {item['retailer']}. "
                f"{deal_reason}. "
                f"Market range: ${market_low:.0f}–${market_high:.0f} across {len(priced)} retailers."
            ),
            "original_price": item["original_price"] or market_high,
            "sale_price": current_price,
            "discount_pct": display_discount,
            "votes": 0,
            "community_validated": False,
            "price_beat_retailer": is_officeworks,
            "is_cheapest": is_cheapest,
            "market_low": market_low,
            "market_high": market_high,
            "market_median": market_median,
            "retailer_count": len(priced),
            "deal_reason": deal_reason,
            "published": "",
            "fetched_at": now,
        })

    return deals


def fetch_retailer_deals() -> list[dict]:
    """
    For each product in SEARCH_QUERIES, run ONE Google Shopping search that returns
    prices from all retailers simultaneously. Compare prices to find genuine discounts.

    Serper budget: 1 call × len(SEARCH_QUERIES) per run.
    With 3 products + 2 runs/day = ~180 calls/month (free tier = 2500/month).
    """
    if not SERPER_ENABLED:
        logger.info("Serper disabled (SERPER_ENABLED=False) — skipping retailer price check")
        return []

    api_key = _get_api_key()
    if not api_key:
        return []

    all_deals = []
    seen_urls: set[str] = set()

    for product_query in SEARCH_QUERIES:
        logger.info(f"Shopping price check: '{product_query}'")
        results = _fetch_shopping_results(product_query, api_key)
        deals = _analyse_prices(product_query, results)

        fresh = [d for d in deals if d["url"] not in seen_urls]
        seen_urls.update(d["url"] for d in fresh)
        all_deals.extend(fresh)

        logger.info(f"  → {len(fresh)} deals found for '{product_query}'")
        time.sleep(0.5)

    logger.info(f"Retailers total: {len(all_deals)} genuine deals across {len(SEARCH_QUERIES)} products")
    return all_deals
