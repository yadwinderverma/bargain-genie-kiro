"""
Direct retailer scrapers for Australian stores.
Uses requests + BeautifulSoup. No API key needed.
Scrapes sale/clearance pages for deals.
"""

import logging
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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 2  # Be polite to servers


def _parse_price(text: str) -> Optional[float]:
    """Parse price string to float."""
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


def _get_page(url: str) -> Optional[BeautifulSoup]:
    """Fetch a page and return BeautifulSoup object."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# JB Hi-Fi scraper
# ---------------------------------------------------------------------------

def scrape_jbhifi() -> list[dict]:
    """Scrape JB Hi-Fi sale page."""
    deals = []
    url = "https://www.jbhifi.com.au/collections/sale?sort_by=price-ascending"
    logger.info(f"Scraping JB Hi-Fi: {url}")

    soup = _get_page(url)
    if not soup:
        return deals

    # JB Hi-Fi product cards
    products = soup.select("div.product-tile, article.product-item, div[data-product-id]")
    logger.info(f"JB Hi-Fi: found {len(products)} product elements")

    for product in products[:30]:  # Limit to first 30
        try:
            # Title
            title_el = product.select_one("h3, h2, .product-title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else ""

            # Link
            link_el = product.select_one("a[href]")
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = f"https://www.jbhifi.com.au{href}" if href.startswith("/") else href

            # Prices
            sale_el = product.select_one(".sale-price, .price--sale, [class*='sale'], [class*='current']")
            orig_el = product.select_one(".original-price, .price--compare, [class*='compare'], s, del")

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
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"Error parsing JB Hi-Fi product: {e}")
            continue

    logger.info(f"JB Hi-Fi: {len(deals)} deals found")
    return deals


# ---------------------------------------------------------------------------
# Kogan scraper
# ---------------------------------------------------------------------------

def scrape_kogan() -> list[dict]:
    """Scrape Kogan clearance/sale page."""
    deals = []
    url = "https://www.kogan.com/au/shop/?sort=discount&category=all"
    logger.info(f"Scraping Kogan: {url}")

    time.sleep(DELAY_BETWEEN_REQUESTS)
    soup = _get_page(url)
    if not soup:
        return deals

    products = soup.select("div[class*='ProductCard'], div[class*='product-card'], li[class*='product']")
    logger.info(f"Kogan: found {len(products)} product elements")

    for product in products[:30]:
        try:
            title_el = product.select_one("h3, h2, [class*='title'], [class*='name']")
            title = title_el.get_text(strip=True) if title_el else ""

            link_el = product.select_one("a[href]")
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = f"https://www.kogan.com{href}" if href.startswith("/") else href

            # Look for discount badge
            discount_el = product.select_one("[class*='discount'], [class*='badge'], [class*='saving']")
            discount_text = discount_el.get_text(strip=True) if discount_el else ""
            discount_pct = None
            match = re.search(r"(\d+)\s*%", discount_text)
            if match:
                discount_pct = float(match.group(1))

            sale_el = product.select_one("[class*='sale-price'], [class*='current-price'], [class*='price']")
            orig_el = product.select_one("s, del, [class*='was'], [class*='original']")

            sale_price = _parse_price(sale_el.get_text(strip=True)) if sale_el else None
            original_price = _parse_price(orig_el.get_text(strip=True)) if orig_el else None

            if discount_pct is None:
                discount_pct = _calculate_discount(original_price, sale_price)

            if not title or not link:
                continue
            if discount_pct is None or discount_pct < MIN_DISCOUNT_PERCENT:
                continue

            deals.append({
                "id": f"kogan_{abs(hash(link))}",
                "source": "kogan",
                "title": title,
                "url": link,
                "description": f"{title} — Kogan sale",
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": 0,
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"Error parsing Kogan product: {e}")
            continue

    logger.info(f"Kogan: {len(deals)} deals found")
    return deals


# ---------------------------------------------------------------------------
# Catch.com.au scraper
# ---------------------------------------------------------------------------

def scrape_catch() -> list[dict]:
    """Scrape Catch.com.au deals page."""
    deals = []
    url = "https://www.catch.com.au/deals/"
    logger.info(f"Scraping Catch: {url}")

    time.sleep(DELAY_BETWEEN_REQUESTS)
    soup = _get_page(url)
    if not soup:
        return deals

    products = soup.select("div[class*='product'], article[class*='product'], div[class*='tile']")
    logger.info(f"Catch: found {len(products)} product elements")

    for product in products[:30]:
        try:
            title_el = product.select_one("h3, h2, [class*='title'], [class*='name']")
            title = title_el.get_text(strip=True) if title_el else ""

            link_el = product.select_one("a[href]")
            link = ""
            if link_el:
                href = link_el.get("href", "")
                link = f"https://www.catch.com.au{href}" if href.startswith("/") else href

            sale_el = product.select_one("[class*='sale'], [class*='current'], [class*='now']")
            orig_el = product.select_one("s, del, [class*='was'], [class*='rrp'], [class*='original']")

            sale_price = _parse_price(sale_el.get_text(strip=True)) if sale_el else None
            original_price = _parse_price(orig_el.get_text(strip=True)) if orig_el else None
            discount_pct = _calculate_discount(original_price, sale_price)

            if not title or not link:
                continue
            if discount_pct is None or discount_pct < MIN_DISCOUNT_PERCENT:
                continue

            deals.append({
                "id": f"catch_{abs(hash(link))}",
                "source": "catch",
                "title": title,
                "url": link,
                "description": f"{title} — Catch.com.au deal",
                "original_price": original_price,
                "sale_price": sale_price,
                "discount_pct": discount_pct,
                "votes": 0,
                "published": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"Error parsing Catch product: {e}")
            continue

    logger.info(f"Catch: {len(deals)} deals found")
    return deals


def fetch_retailer_deals() -> list[dict]:
    """Run all retailer scrapers and combine results."""
    all_deals = []
    all_deals.extend(scrape_jbhifi())
    all_deals.extend(scrape_kogan())
    all_deals.extend(scrape_catch())
    logger.info(f"Retailers total: {len(all_deals)} deals")
    return all_deals
