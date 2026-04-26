"""
Configuration for the Bargain Hunter bot.
Adjust these settings to customise what deals you're looking for.
"""

# --- Deal Thresholds ---
MIN_DISCOUNT_PERCENT = 50       # Minimum % off to consider a deal (non-OzBargain sources)
MIN_OZBARGAIN_VOTES = 10        # Minimum upvotes on OzBargain to consider
LLM_MIN_SCORE = 6               # Minimum LLM score (out of 10) to send alert

# --- OzBargain Trust Settings ---
# OzBargain deals are community-validated — if something made it onto the site
# with enough votes, it's generally a real deal worth looking at.
OZBARGAIN_TRUSTED = True        # Treat OzBargain as a trusted source
OZBARGAIN_SCORE_BOOST = 2       # Add this to LLM score for OzBargain deals
OZBARGAIN_MIN_VOTES_TRUSTED = 5 # Votes needed to get the trust boost (lower bar than general filter)

# --- Search Terms for Google Shopping via Serper ---
# Add or remove product categories you care about
SEARCH_QUERIES = [
    "beats powerbeats pro 2",
    "shokz openfit 2",
    "bose ultra open earbuds",
]

# --- OzBargain RSS ---
OZBARGAIN_RSS_URL = "https://www.ozbargain.com.au/deals/feed"
OZBARGAIN_MAX_ITEMS = 50        # How many recent deals to pull

# --- Retailers to scrape directly (optional, extend as needed) ---
RETAILER_URLS = {
    "jbhifi": "https://www.jbhifi.com.au/collections/sale",
    "kogan": "https://www.kogan.com/au/shop/?sort=discount",
}

# --- Cache ---
CACHE_FILE = "data/deals_cache.json"
CACHE_MAX_AGE_DAYS = 7          # Remove deals older than this from cache

# --- Slack ---
SLACK_CHANNEL_NAME = "#bargains"  # Just for display in logs
MAX_SLACK_ALERTS_PER_RUN = 10    # Cap alerts per run to avoid spam

# --- LLM ---
LLM_MODEL = "gemini-2.5-flash"   # Free Gemini model (gemini-2.0-flash is current free tier)
LLM_MAX_DEALS_PER_BATCH = 5      # Deals per LLM API call (batching saves quota)
