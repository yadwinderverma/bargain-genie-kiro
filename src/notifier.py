"""
Slack notifier — sends deal alerts to your Slack channel via Incoming Webhooks.
Uses Slack Block Kit for rich, readable messages.

Set up:
1. Go to https://api.slack.com/apps → Create New App → From scratch
2. Enable Incoming Webhooks
3. Add webhook to your workspace and channel
4. Copy the webhook URL to SLACK_WEBHOOK_URL secret in GitHub
"""

import logging
import os
from datetime import datetime, timezone

import requests

from config import MAX_SLACK_ALERTS_PER_RUN, SLACK_CHANNEL_NAME

logger = logging.getLogger(__name__)

SOURCE_EMOJI = {
    "ozbargain":   "🔥",
    "jbhifi":      "🎵",
    "kogan":       "🛒",
    "catch":       "🎣",
    "officeworks": "🖊️",
    "bigw":        "🏪",
    "target":      "🎯",
    "amazon":      "📦",
    "serper_shopping": "🔍",
}

SCORE_EMOJI = {
    range(9, 11): "🏆",
    range(7, 9): "⭐",
    range(5, 7): "👍",
}


def _get_webhook_url() -> str | None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.error("SLACK_WEBHOOK_URL not set — cannot send Slack notifications")
    return url


def _get_source_emoji(source: str) -> str:
    for key, emoji in SOURCE_EMOJI.items():
        if key in source.lower():
            return emoji
    return "💰"


def _get_score_emoji(score: int) -> str:
    for score_range, emoji in SCORE_EMOJI.items():
        if score in score_range:
            return emoji
    return "💡"


def _format_price(price: float | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:,.2f}"


def _build_deal_block(deal: dict) -> list[dict]:
    """Build Slack Block Kit blocks for a single deal."""
    title = deal.get("title", "Unknown Deal")
    url = deal.get("url", "")
    source = deal.get("source", "unknown")
    original_price = deal.get("original_price")
    sale_price = deal.get("sale_price")
    discount_pct = deal.get("discount_pct")
    votes = deal.get("votes", 0)
    llm_score = deal.get("llm_score", 0)
    llm_reason = deal.get("llm_reason", "")
    llm_category = deal.get("llm_category", "General")

    source_emoji = _get_source_emoji(source)
    score_emoji = _get_score_emoji(llm_score)

    # Build price display
    price_parts = []
    if sale_price:
        price_parts.append(f"*{_format_price(sale_price)}*")
    if original_price and sale_price and original_price != sale_price:
        price_parts.append(f"~{_format_price(original_price)}~")
    if discount_pct:
        price_parts.append(f"*{discount_pct:.0f}% OFF*")
    price_text = "  ".join(price_parts) if price_parts else "Price not available"

    # Build context line
    context_parts = [f"{source_emoji} {source.replace('_', ' ').title()}"]
    if deal.get("community_validated"):
        context_parts.append("🏅 OzBargain Community Pick")
    if deal.get("price_beat_retailer"):
        context_parts.append("🔖 Price Beat Guarantee")
    if votes > 0:
        context_parts.append(f"👍 {votes} votes")
    context_parts.append(f"{score_emoji} AI Score: {llm_score}/10")
    if llm_category:
        context_parts.append(f"📦 {llm_category}")

    title_text = f"<{url}|{title}>" if url else title

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{title_text}\n{price_text}",
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "  |  ".join(context_parts)},
            ],
        },
    ]

    if llm_reason:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"💬 _{llm_reason}_"},
            ],
        })

    blocks.append({"type": "divider"})
    return blocks


def _build_summary_header(deals: list[dict], run_time: str) -> list[dict]:
    """Build the header block for the Slack message."""
    count = len(deals)
    sources = list({d.get("source", "").split("_")[0] for d in deals})
    sources_text = ", ".join(s.title() for s in sources if s)

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🛍️ {count} Bargain{'s' if count != 1 else ''} Found!",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"📅 {run_time}  |  Sources: {sources_text or 'Various'}  |  Min 50% off",
                }
            ],
        },
        {"type": "divider"},
    ]


def send_slack_alerts(deals: list[dict]) -> bool:
    """
    Send deal alerts to Slack.
    Returns True if successful, False otherwise.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return False

    if not deals:
        logger.info("No deals to send to Slack")
        return True

    # Cap alerts per run
    if len(deals) > MAX_SLACK_ALERTS_PER_RUN:
        logger.info(f"Capping alerts at {MAX_SLACK_ALERTS_PER_RUN} (had {len(deals)})")
        # Sort by score descending, take top N
        deals = sorted(deals, key=lambda d: d.get("llm_score", 0), reverse=True)
        deals = deals[:MAX_SLACK_ALERTS_PER_RUN]

    run_time = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")
    blocks = _build_summary_header(deals, run_time)

    for deal in deals:
        blocks.extend(_build_deal_block(deal))

    # Slack has a 50-block limit per message — split if needed
    MAX_BLOCKS = 50
    block_chunks = [blocks[i : i + MAX_BLOCKS] for i in range(0, len(blocks), MAX_BLOCKS)]

    success = True
    for chunk_idx, chunk in enumerate(block_chunks):
        payload = {
            "blocks": chunk,
            "text": f"🛍️ {len(deals)} bargains found! Check the channel for details.",
        }

        try:
            response = requests.post(webhook_url, json=payload, timeout=15)
            response.raise_for_status()
            logger.info(f"Slack message {chunk_idx + 1}/{len(block_chunks)} sent successfully")
        except requests.RequestException as e:
            logger.error(f"Failed to send Slack message chunk {chunk_idx + 1}: {e}")
            success = False

    return success


def send_slack_no_deals_message() -> None:
    """Send a brief 'no deals found' message (optional, can be disabled)."""
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return

    # Only send this if you want to confirm the bot ran — comment out to stay quiet
    # payload = {
    #     "text": "🔍 Bargain Hunter ran — no deals meeting criteria found this time."
    # }
    # requests.post(webhook_url, json=payload, timeout=15)
    logger.info("No deals to report — Slack not notified (silent run)")


def send_slack_error_message(error: str) -> None:
    """Send an error alert to Slack so you know the bot failed."""
    webhook_url = _get_webhook_url()
    if not webhook_url:
        return

    payload = {
        "text": f"⚠️ *Bargain Hunter Error*\n```{error[:500]}```\nCheck GitHub Actions logs for details.",
    }
    try:
        requests.post(webhook_url, json=payload, timeout=15)
    except requests.RequestException as e:
        logger.error(f"Failed to send error message to Slack: {e}")
