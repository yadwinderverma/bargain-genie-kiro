"""
Retailer deal fetcher for Australian stores.

Direct scraping of Kogan/Catch is blocked (403). Instead we use Serper.dev
to search Google for deals on specific retailer sites — much more reliable.
JB Hi-Fi is attempted via direct scrape first (they're more permissive),
with a Serper fallback if it returns nothing.
"""

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import MIN_DISCOUNT_PERCENT

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT = 15

# Retailer sites to search via Serper when direct scraping isn't possible
RETAILER_SERPER_QUERIES = [
    ("jbhifi",  "sale -50% off site:jbhifi.com.au"),
    ("kogan",   "50% off clearance sale site:kogan.com"),
    ("catch",   "50% off deals site:catch.com.au"),
    ("amazon",  "50% off deals site:amazon.com.au"),
    ("bigw",    "50% off sale site:bigw.com.au"),
    ("target",  "50% off sale site:target.com.au"),
]


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _calculate_discount(original: Optional[float], sale: Optional[float]) -> Optional[float]:
    if original and sale and original > 0 and sale < original:
        return round((1 - sale / original) * 100, 1)
    return None


def _extract_discount_from_text(text: str) -> Optional[float]:
    match = re.search(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    if re.search(r"half[\s-]?price", text, re.IGNORECASE):
        return 50.0
    return None


# ---------------------------------------------------------------------------
# Serper-based retailer search (primary method for blocked sites)
# ---------------------------------------------------------------------------

def _serper_search_retailer(retailer_name: str, query: str, api_key: str) -> list[dict]:
    """
    Use Serper web search to find deals on a specific retailer site.
    Returns parsed deal dicts.
    """
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "gl": "au",
        "hl": "en",
        "num": 10,
    }

    deals = []
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            json=payload,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("organic", [])

        for item in results:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")

            if not title or not link:
                continue

            # Try to extract discount from title or snippet
            discount_pct = _extract_discount_from_text(title) or _extract_discount_from_text(snippet)

            # Try to extract prices from snippet
            prices = re.findall(r"\$[\d,]+(?:\.\d{2})?", snippet)
            prices_clean = []
            for p in prices:
                try:
                    prices_clean.append(float(p.replace("$", "").replace(",", "")))
                except ValueError:
                    pass

            original_price = None
            sale_price = None
            if len(prices_clean) >= 2:
                original_price = max(prices_clean[:4])
                sale_price = min(prices_clean[:4])
                if discount_pct is None and original_price > 0:
                    discount_pct = _calculate_discount(original_price, sale_price)
            elif len(prices_clean) == 1:
                sale_price = prices_clean[0]

            if discount_pct is None or discount_pct < MIN_DISCOUNT_PERCENT:
                continue

            deals.append({
                "id": f"{retailer_name}_{abs(hash(link))}",
                "source": retailer_name,
                "title": title,
                "url": link,
                "description": snippet[:300],
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": 0,
                "community_validated": False,
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })

    except requests.RequestException as e:
        logger.error(f"Serper retailer search failed for {retailer_name}: {e}")

    return deals


# ---------------------------------------------------------------------------
# JB Hi-Fi direct scrape (attempt first, fall back to Serper)
# ---------------------------------------------------------------------------

def _scrape_jbhifi_direct() -> list[dict]:
    """Attempt direct scrape of JB Hi-Fi sale page."""
    deals = []
    url = "https://www.jbhifi.com.au/collections/sale?sort_by=best-selling"

    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"JB Hi-Fi direct scrape failed: {e}")
        return deals

    soup = BeautifulSoup(response.text, "html.parser")
    products = soup.select(
        "div.product-tile, article.product-item, "
        "div[data-product-id], div[class*='ProductCard']"
    )
    logger.info(f"JB Hi-Fi direct: found {len(products)} product elements")

    for product in products[:30]:
        try:
            title_el = product.select_one("h3, h2, [class*='title'], [class*='name']")
            title = title_el.get_text(strip=True) if title_el else ""

            link_el = product.select_one("a[href]")
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = f"https://www.jbhifi.com.au{href}" if href.startswith("/") else href

            sale_el = product.select_one(
                ".sale-price, .price--sale, [class*='sale'], [class*='current']"
            )
            orig_el = product.select_one(
                ".original-price, .price--compare, [class*='compare'], s, del"
            )

            sale_price = _parse_price(sale_el.get_text(strip=True)) if sale_el else None
            original_price = _parse_price(orig_el.get_text(strip=True)) if orig_el else None
            discount_pct = _calculate_discount(original_price, sale_price)

            if not title or not link:
                continue
            if discount_pct is None or discount_pct < MIN_DISCOUNT_PERCENT:
                continue

            deals.append({
                "id": f"jbhifi_{abs(hash(link))}",
                "source": "jbhifi",
                "title": title,
                "url": link,
                "description": f"{title} — JB Hi-Fi sale",
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": 0,
                "community_validated": False,
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"Error parsing JB Hi-Fi product: {e}")

    return deals


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_retailer_deals() -> list[dict]:
    """
    Fetch retailer deals. Uses Serper for sites that block scrapers,
    and attempts direct scrape for JB Hi-Fi with Serper fallback.
    """
    api_key = os.environ.get("SERPER_API_KEY")
    all_deals = []

    # --- JB Hi-Fi: try direct scrape first ---
    logger.info("Fetching JB Hi-Fi deals (direct scrape)...")
    jb_deals = _scrape_jbhifi_direct()
    logger.info(f"JB Hi-Fi direct: {len(jb_deals)} deals")

    # If direct scrape got nothing and we have Serper, fall back
    if not jb_deals and api_key:
        logger.info("JB Hi-Fi direct scrape empty — falling back to Serper search")
        jb_deals = _serper_search_retailer(
            "jbhifi", "sale -50% off site:jbhifi.com.au", api_key
        )
        logger.info(f"JB Hi-Fi via Serper: {len(jb_deals)} deals")

    all_deals.extend(jb_deals)

    # --- Other retailers: Serper only (they block scrapers) ---
    if api_key:
        for retailer_name, query in RETAILER_SERPER_QUERIES[1:]:  # Skip jbhifi, already done
            logger.info(f"Searching {retailer_name} via Serper...")
            deals = _serper_search_retailer(retailer_name, query, api_key)
            logger.info(f"{retailer_name}: {len(deals)} deals")
            all_deals.extend(deals)
            time.sleep(0.5)  # Small delay between API calls
    else:
        logger.warning(
            "SERPER_API_KEY not set — skipping Kogan, Catch, Amazon AU, Big W, Target searches. "
            "Add SERPER_API_KEY to GitHub secrets to enable these."
        )

    logger.info(f"Retailers total: {len(all_deals)} deals")
    return all_deals
