# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

LoyalCorp P1 — a Telegram bot for running automated Press-1 IVR phone campaigns. Users upload lead lists, buy credits via Oxapay (USDT), then launch campaigns that dial numbers through Asterisk and detect keypress responses.

## Running the Services

Both services run from `/opt/loyalcorp/` on the server:

```bash
# Start/stop/status
systemctl start loyalcorp-bot       # Telegram bot + webhook server
systemctl start loyalcorp-worker    # Campaign dialer

journalctl -fu loyalcorp-bot        # Live logs
journalctl -fu loyalcorp-worker

# Run manually for development
cd /opt/loyalcorp
venv/bin/python bot/main.py
venv/bin/python dialer/campaign_worker.py
```

## Install Dependencies

```bash
venv/bin/pip install -r bot/requirements.txt       # python-telegram-bot, asyncpg, aiohttp
venv/bin/pip install -r dialer/requirements.txt    # asyncpg
```

## Database

```bash
# Apply schema (destructive — drops all tables first)
psql -U loyalcorp -d loyalcorp_p1 -f database/schema.sql

# Connect
psql -U loyalcorp -d loyalcorp_p1
```

## Architecture

Two separate processes sharing the same `bot/config.py`:

**`bot/main.py`** — Telegram bot (python-telegram-bot v21, polling mode). Handles all user interaction via commands and inline keyboards. Also spawns a `WebhookServer` (aiohttp on port 8000) in the same process to receive Oxapay payment webhooks and Asterisk call result webhooks.

**`dialer/campaign_worker.py`** — Standalone process that polls PostgreSQL every second for `running` campaigns and originates calls via Asterisk AMI. Adds `../bot` to its sys.path to import from `bot/config.py`. Uses fire-and-forget `asyncio.create_task` per number, bounded by a semaphore (`MAX_CONCURRENT_CALLS`) and each campaign's per-campaign `cps` setting.

**Webhook flow (port 8000):**
- `POST /webhook/oxapay` — Oxapay notifies payment confirmed → credits added → user notified
- `POST /webhook/dtmf` — Asterisk posts after digit press or call end → updates `calls` + `campaign_data` tables, deducts per-minute cost, sends Press-1 DM/group alert
- `POST /webhook/hangup` — Asterisk posts on hangup → finalizes call record

**Asterisk integration:**
- Single global PJSIP endpoint: `loyalcorp_trunk` (defined in `/etc/asterisk/pjsip_loyalcorp.conf`)
- IVR dialplan context: `loyalcorp-p1-ivr` (in `/etc/asterisk/extensions_loyalcorp.conf`)
- Asterisk passes `CALL_ID`, `CAMPAIGN_ID`, `CAMPAIGN_DATA_ID`, `VOICE_FILE`, `OUTRO_FILE`, `WEBHOOK_URL` as channel variables
- Admin can update trunk credentials and reload Asterisk from within the bot (writes the conf file directly via `trunk_handler.py`)

## Database Schema Key Points

- `users.is_active = FALSE` by default — admin must approve before user can act
- `leads` → `lead_numbers` → When a campaign starts, numbers are **copied** into `campaign_data` (so lead edits don't affect running campaigns)
- `campaign_data.status`: `pending → dialing → answered/completed/failed/machine`
- `calls` table is the CDR; `campaign_data` tracks dialing state per number
- `bot_settings` table holds runtime-configurable values (price per 1k, min topup, max CPS) — admin changes these via bot, not by editing config

## Campaign Lifecycle

1. User creates campaign (wizard: name → lead list → CPS → intro sound → outro sound → confirm)
2. On confirm: credits deducted upfront `(total_numbers / 1000 × price_per_1k)`, campaign set to `running`
3. Worker picks it up, copies lead numbers into `campaign_data`, originates calls via AMI
4. Each call result POSTs to `/webhook/dtmf` or `/webhook/hangup` → updates DB counters + deducts per-minute cost ($1/min, 6s billing increments)
5. When all numbers are dialed and no active calls remain, campaign set to `completed` and user notified

**Note:** There is dual billing — credits are charged upfront per-1k-numbers AND again per-minute as calls complete via `_calc_cost()` in `webhook_server.py`. This appears intentional (flat fee covers routing, per-minute covers talk time).

## Configuration

All config lives in `bot/config.py`. The credentials there are hardcoded — they should be treated as secrets and not committed. When changing credentials:
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `ADMIN_TELEGRAM_IDS` — list of Telegram user IDs with admin access
- `OXAPAY_API_KEY` — from oxapay.com dashboard
- `AMI_CONFIG.secret` — must match `/etc/asterisk/manager.conf`
- `WEBHOOK_URL` — must be publicly reachable (Oxapay posts here)

Runtime settings (price, etc.) are in the `bot_settings` DB table, editable via admin panel.

## Sound Files

Audio files for IVR must be placed in `/var/lib/asterisk/sounds/custom/` as `.wav`, `.ulaw`, `.gsm`, `.alaw`, or `.g722`. The bot lists files from this directory during campaign creation. Asterisk's `Playback()` strips the extension — store filenames without extension in `campaigns.voice_file`.
