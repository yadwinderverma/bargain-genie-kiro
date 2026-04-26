"""
Configuration for the Bargain Hunter bot.
Adjust these settings to customise what deals you're looking for.
"""

# --- Products to track ---
# These are the specific products you want to monitor.
# Be specific enough to avoid false matches (e.g. include model number/version).
SEARCH_QUERIES = [
    "beats powerbeats pro 2",
    "shokz openfit 2",
    "bose ultra open earbuds",
    "airpod",
]

# --- Deal Thresholds ---
# For regular retailers: alert if price is at least this % below the highest
# price seen across all retailers (i.e. a genuine price drop vs market).
MIN_DISCOUNT_PERCENT = 20       # 20-30% off vs market price triggers an alert

# For OzBargain: community votes are the signal, not % off
MIN_OZBARGAIN_VOTES = 10        # Minimum upvotes to consider an OzBargain deal

# LLM quality gate — deals scoring below this are not sent to Slack
LLM_MIN_SCORE = 6

# --- OzBargain Trust Settings ---
# If something made it onto OzBargain with votes, alert immediately — the
# community has already validated it. OzBargain deals bypass the price drop
# threshold and go straight to LLM scoring with a trust boost.
OZBARGAIN_TRUSTED = True
OZBARGAIN_SCORE_BOOST = 2       # Added to LLM score for community-validated deals
OZBARGAIN_MIN_VOTES_TRUSTED = 5 # Votes needed for the trust boost

# --- Serper API Budget ---
# Free tier = 2500 searches/month.
# We use 1 Shopping search per product per run = len(SEARCH_QUERIES) × 2 runs/day.
# With 3 products that's ~180 calls/month — well within the free limit.
# Do NOT add per-retailer searches — one Shopping call returns all retailers at once.
SERPER_ENABLED = True           # Set False to disable Serper entirely and rely only on OzBargain

# --- OzBargain RSS ---
OZBARGAIN_RSS_URL = "https://www.ozbargain.com.au/deals/feed"
OZBARGAIN_MAX_ITEMS = 50

# --- Cache ---
CACHE_FILE = "data/deals_cache.json"
CACHE_MAX_AGE_DAYS = 7

# --- Slack ---
SLACK_CHANNEL_NAME = "#bargains"
MAX_SLACK_ALERTS_PER_RUN = 10

# --- LLM ---
LLM_MODEL = "gemini-2.5-flash"  # Free tier — matches google-genai SDK model names
LLM_MAX_DEALS_PER_BATCH = 5
