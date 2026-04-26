"""
Bargain Hunter — main entry point.

Fetches deals from OzBargain, Serper (Google Shopping), and Australian retailers,
filters for genuine 50%+ discounts, scores them with Gemini AI,
and sends the best ones to Slack.
"""

import logging
import sys
import traceback
from datetime import datetime, timezone

from src.analyser import analyse_deals
from src.cache import filter_new_deals, mark_deals_alerted
from src.fetchers.ozbargain import fetch_ozbargain_deals
from src.fetchers.retailers import fetch_retailer_deals
from src.notifier import send_slack_alerts, send_slack_error_message, send_slack_no_deals_message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run() -> int:
    """
    Main pipeline. Returns exit code (0 = success, 1 = error).
    """
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info(f"Bargain Hunter starting at {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    try:
        # ----------------------------------------------------------------
        # Step 1: Fetch deals from all sources
        # ----------------------------------------------------------------
        logger.info("--- Step 1: Fetching deals ---")
        all_deals = []

        ozb_deals = fetch_ozbargain_deals()
        logger.info(f"OzBargain: {len(ozb_deals)} deals")
        all_deals.extend(ozb_deals)

        retailer_deals = fetch_retailer_deals()
        logger.info(f"Retailers (Shopping): {len(retailer_deals)} deals")
        all_deals.extend(retailer_deals)

        logger.info(f"Total deals fetched: {len(all_deals)}")

        if not all_deals:
            logger.info("No deals found from any source")
            send_slack_no_deals_message()
            return 0

        # ----------------------------------------------------------------
        # Step 2: Deduplicate against cache (skip already-seen deals)
        # ----------------------------------------------------------------
        logger.info("--- Step 2: Filtering new deals ---")
        new_deals = filter_new_deals(all_deals)
        logger.info(f"New deals (not seen before): {len(new_deals)}")

        if not new_deals:
            logger.info("All deals already seen — nothing new to report")
            send_slack_no_deals_message()
            return 0

        # ----------------------------------------------------------------
        # Step 3: LLM analysis — score and filter genuine bargains
        # ----------------------------------------------------------------
        logger.info("--- Step 3: LLM analysis ---")
        quality_deals = analyse_deals(new_deals)
        logger.info(f"Quality deals after LLM filter: {len(quality_deals)}")

        if not quality_deals:
            logger.info("No deals passed LLM quality filter")
            send_slack_no_deals_message()
            return 0

        # Sort by LLM score descending
        quality_deals.sort(key=lambda d: d.get("llm_score", 0), reverse=True)

        # ----------------------------------------------------------------
        # Step 4: Send to Slack
        # ----------------------------------------------------------------
        logger.info("--- Step 4: Sending Slack alerts ---")
        success = send_slack_alerts(quality_deals)

        if success:
            mark_deals_alerted(quality_deals)
            logger.info(f"Successfully alerted {len(quality_deals)} deals to Slack")
        else:
            logger.error("Slack notification failed")
            return 1

        # ----------------------------------------------------------------
        # Summary
        # ----------------------------------------------------------------
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"Run complete in {elapsed:.1f}s")
        logger.info(f"  Fetched:   {len(all_deals)} deals (OzBargain + Shopping)")
        logger.info(f"  New:       {len(new_deals)} deals")
        logger.info(f"  Alerted:   {len(quality_deals)} deals")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"Unhandled error:\n{error_msg}")
        send_slack_error_message(error_msg)
        return 1


if __name__ == "__main__":
    sys.exit(run())
