# 🛍️ Bargain Hunter

Automated deal finder for Australian shoppers. Runs twice daily via GitHub Actions, searches for 50%+ discounts across OzBargain, JB Hi-Fi, Kogan, Catch, and Google Shopping, then sends the best deals to your Slack channel.

## How It Works

```
GitHub Actions (8am + 8pm AEST)
    ↓
Fetch deals from:
  • OzBargain RSS feed (community-voted deals)
  • Serper.dev Google Shopping API
  • JB Hi-Fi, Kogan, Catch scrapers
    ↓
Filter: only new deals (not seen before)
    ↓
Gemini AI scores each deal 1–10
  (checks for fake discounts, rates genuine value)
    ↓
Top deals (score ≥ 6) → Slack alert
    ↓
Cache updated in repo (prevents duplicate alerts)
```

## Setup

### 1. Fork this repository

### 2. Get your free API keys

| Service | Where to get it | Free tier |
|---|---|---|
| **Serper.dev** | [serper.dev](https://serper.dev) | 2,500 searches/month |
| **Google Gemini** | [aistudio.google.com](https://aistudio.google.com/app/apikey) | 15 req/min, 1,500/day |
| **Slack Webhook** | Your Slack app settings (see below) | Free |

### 3. Set up Slack Incoming Webhook

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it "Bargain Hunter", select your workspace
3. Go to **Incoming Webhooks** → toggle **On**
4. Click **Add New Webhook to Workspace** → select your channel
5. Copy the webhook URL

### 4. Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `SERPER_API_KEY` | Your Serper.dev API key |
| `GEMINI_API_KEY` | Your Google AI Studio API key |
| `SLACK_WEBHOOK_URL` | Your Slack webhook URL |

### 5. Enable GitHub Actions

Go to the **Actions** tab in your repo and enable workflows if prompted.

### 6. Test it

Go to **Actions → Bargain Hunter → Run workflow** to trigger a manual run.

## Configuration

Edit `config.py` to customise:

```python
MIN_DISCOUNT_PERCENT = 50       # Minimum % off (default: 50%)
MIN_OZBARGAIN_VOTES = 10        # Min community votes on OzBargain
LLM_MIN_SCORE = 6               # Min AI score to send alert (1-10)
MAX_SLACK_ALERTS_PER_RUN = 10   # Max alerts per run (avoid spam)

SEARCH_QUERIES = [              # What to search for
    "electronics deals Australia",
    "laptop deals Australia",
    # Add your own...
]
```

## Schedule

Runs at:
- **8:00 AM AEST** (22:00 UTC previous day)
- **8:00 PM AEST** (10:00 UTC)

To change the schedule, edit the `cron` values in `.github/workflows/bargain_hunt.yml`.

## Project Structure

```
bargain-hunter/
├── .github/workflows/
│   └── bargain_hunt.yml      # GitHub Actions workflow
├── src/
│   ├── fetchers/
│   │   ├── ozbargain.py      # OzBargain RSS parser
│   │   ├── serper.py         # Google Shopping via Serper API
│   │   └── retailers.py      # JB Hi-Fi, Kogan, Catch scrapers
│   ├── analyser.py           # Gemini AI deal scoring
│   ├── cache.py              # Deduplication (JSON file in repo)
│   └── notifier.py           # Slack Block Kit messages
├── data/
│   └── deals_cache.json      # Auto-updated by bot
├── config.py                 # All settings in one place
├── main.py                   # Entry point
└── requirements.txt
```

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SERPER_API_KEY="your_key"
export GEMINI_API_KEY="your_key"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."

# Run
python main.py
```

## Notes

- The `data/deals_cache.json` file is committed back to the repo after each run to track seen deals
- Retailer scrapers may break if sites update their HTML — check logs if a source stops working
- OzBargain RSS is the most reliable source (no scraping, community-validated)
- Gemini AI checks for "fake" discounts where retailers inflate the original price
