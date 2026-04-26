"""
Retailer price fetcher — uses Serper Shopping API for structured price data.

Strategy:
1. For each product in SEARCH_QUERIES, run ONE Google Shopping search
2. Filter results to TRUSTED_RETAILERS only (no Cash Converters, random sellers etc.)
3. Filter results to only those matching the product keywords exactly
4. Use the median of TRUSTED retailer prices as the market baseline (not the inflated max)
5. Flag anything MIN_DISCOUNT_PERCENT% below that baseline
6. Officeworks: flag if they're cheapest (price-beat signal)
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

# ONLY these retailers are trusted. Anything else (Cash Converters, random
# marketplaces, overseas sellers, etc.) is filtered out entirely.
TRUSTED_RETAILERS: dict[str, str] = {
    "jbhifi.com.au":       "JB Hi-Fi",
    "officeworks.com.au":  "Officeworks",
    "amazon.com.au":       "Amazon AU",
    "bigw.com.au":         "Big W",
    "target.com.au":       "Target AU",
    "kogan.com":           "Kogan",
    "catch.com.au":        "Catch",
    "harveynorman.com.au": "Harvey Norman",
    "costco.com.au":       "Costco AU",
    "themarket.com":       "The Market",
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


def _match_trusted_retailer(source: str) -> Optional[str]:
    """
    Return the clean retailer name if source matches a trusted retailer, else None.
    This is the gatekeeper — anything not in TRUSTED_RETAILERS is dropped.
    """
    source_lower = source.lower()
    for domain, name in TRUSTED_RETAILERS.items():
        if domain in source_lower:
            return name
    return None


def _is_officeworks(source: str) -> bool:
    return "officeworks" in source.lower()


def _matches_product(title: str, query: str) -> bool:
    """
    All keywords in the query must appear in the title.
    e.g. "shokz openfit 2" requires 'shokz', 'openfit', '2' all in title.
    This prevents "Shokz OpenComm 2" or "OpenFit Air" from matching "shokz openfit 2".
    """
    title_lower = title.lower()
    return all(kw in title_lower for kw in query.lower().split())


def _fetch_shopping_results(query: str, api_key: str) -> list[dict]:
    """Run a Google Shopping search — 1 call per product."""
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": f"{query} Australia",
        "gl": "au",
        "hl": "en",
        "num": 20,
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
    Filter to trusted retailers + exact product match, then find genuine discounts.
    Uses median of trusted-retailer prices as the market baseline — not the max,
    which is often an inflated outlier from aggregator sites.
    """
    if not results:
        return []

    trusted = []
    skipped_retailer = 0
    skipped_product = 0

    for item in results:
        source = item.get("source", "")
        title = item.get("title", "")
        link = item.get("link", "")
        price_str = item.get("price", "")
        original_str = item.get("originalPrice", "")

        # Gate 1: trusted retailer only
        retailer_name = _match_trusted_retailer(source)
        if not retailer_name:
            logger.debug(f"  Skipping untrusted seller: '{source}' — '{title[:50]}'")
            skipped_retailer += 1
            continue

        # Gate 2: must actually be the product we searched for
        if not _matches_product(title, query):
            logger.debug(f"  Skipping wrong product at {retailer_name}: '{title[:60]}'")
            skipped_product += 1
            continue

        current_price = _parse_price(price_str)
        if not current_price or not link:
            continue

        # Gate 3: ignore suspiciously high "original" prices (likely aggregator noise).
        # If originalPrice is more than 2× the current price, discard it — it's not real.
        raw_original = _parse_price(original_str)
        original_price = None
        if raw_original and raw_original <= current_price * 2.0:
            original_price = raw_original

        trusted.append({
            "source": source,
            "retailer": retailer_name,
            "title": title,
            "link": link,
            "current_price": current_price,
            "original_price": original_price,
            "is_officeworks": _is_officeworks(source),
        })

    logger.info(
        f"'{query}' — {len(results)} results: "
        f"{len(trusted)} trusted, {skipped_retailer} untrusted sellers skipped, "
        f"{skipped_product} wrong products skipped"
    )

    if not trusted:
        return []

    prices = sorted(p["current_price"] for p in trusted)
    market_low = prices[0]
    market_median = prices[len(prices) // 2]
    # Use median as the "normal" price — much more stable than max which can be wildly inflated
    logger.info(
        f"  Trusted price range: low=${market_low:.0f}, "
        f"median=${market_median:.0f} across {len(trusted)} retailers"
    )

    deals = []
    now = datetime.now(timezone.utc).isoformat()

    for item in trusted:
        current_price = item["current_price"]
        original_price = item["original_price"]
        is_officeworks = item["is_officeworks"]

        # Discount vs item's own stated original price (already sanity-checked above)
        own_discount = None
        if original_price and original_price > current_price:
            own_discount = round((1 - current_price / original_price) * 100, 1)

        # Discount vs median trusted-retailer price
        vs_median = round((1 - current_price / market_median) * 100, 1) if market_median > 0 else 0

        is_cheapest = current_price == market_low
        is_near_cheapest = current_price <= market_low * 1.05

        should_include = False
        deal_reason = ""

        if is_officeworks:
            # Officeworks: flag if cheapest or near-cheapest — price-beat guarantee
            # means this is likely the best available price in AU
            if is_cheapest:
                should_include = True
                deal_reason = f"Cheapest in AU at ${current_price:.0f} — Officeworks price-beat guarantee"
            elif is_near_cheapest:
                should_include = True
                deal_reason = f"Near-cheapest at ${current_price:.0f} (within 5% of market low ${market_low:.0f})"
            elif vs_median >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"{vs_median:.0f}% below median market price of ${market_median:.0f}"
        else:
            # Other trusted retailers: need a real discount
            if own_discount is not None and own_discount >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"{own_discount:.0f}% off (${original_price:.0f} → ${current_price:.0f})"
            elif vs_median >= MIN_DISCOUNT_PERCENT:
                should_include = True
                deal_reason = f"{vs_median:.0f}% below median market price of ${market_median:.0f}"

        if not should_include:
            logger.debug(
                f"  Skip {item['retailer']}: ${current_price:.0f} "
                f"(own={own_discount}%, vs_median={vs_median:.0f}%)"
            )
            continue

        display_discount = own_discount if own_discount else (vs_median if vs_median > 0 else None)

        deals.append({
            "id": f"retail_{abs(hash(item['link']))}",
            "source": item["retailer"].lower().replace(" ", "_"),
            "title": item["title"],
            "url": item["link"],
            "description": (
                f"{item['title']} at {item['retailer']}. {deal_reason}. "
                f"Checked {len(trusted)} trusted AU retailers: "
                f"low=${market_low:.0f}, median=${market_median:.0f}."
            ),
            "original_price": original_price or market_median,
            "sale_price": current_price,
            "discount_pct": display_discount,
            "votes": 0,
            "community_validated": False,
            "price_beat_retailer": is_officeworks,
            "is_cheapest": is_cheapest,
            "market_low": market_low,
            "market_median": market_median,
            "retailer_count": len(trusted),
            "deal_reason": deal_reason,
            "published": "",
            "fetched_at": now,
        })

    return deals


def fetch_retailer_deals() -> list[dict]:
    """
    For each product in SEARCH_QUERIES, run ONE Google Shopping search.
    Serper budget: 1 call × len(SEARCH_QUERIES) per run.
    3 products × 2 runs/day = ~180 calls/month (free tier = 2500/month).
    """
    if not SERPER_ENABLED:
        logger.info("Serper disabled — skipping retailer price check")
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
        logger.info(f"  → {len(fresh)} quality deals for '{product_query}'")
        time.sleep(0.5)

    logger.info(f"Retailers total: {len(all_deals)} deals across {len(SEARCH_QUERIES)} products")
    return all_deals
