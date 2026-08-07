# LoyalCorp P1 — Setup Guide

## 1. Server Requirements
- Ubuntu 20.04+
- Python 3.9+
- PostgreSQL 13+
- Asterisk 18+

## 2. Database Setup
```bash
sudo -u postgres psql
CREATE USER loyalcorp WITH PASSWORD 'loyalcorp2026';
CREATE DATABASE loyalcorp_p1 OWNER loyalcorp;
\q

psql -U loyalcorp -d loyalcorp_p1 -f /opt/loyalcorp/database/schema.sql
```

## 3. Python Environment
```bash
mkdir -p /opt/loyalcorp
cp -r bot/ /opt/loyalcorp/bot/
cp -r dialer/ /opt/loyalcorp/dialer/
cp -r database/ /opt/loyalcorp/database/

python3 -m venv /opt/loyalcorp/venv
/opt/loyalcorp/venv/bin/pip install -r /opt/loyalcorp/bot/requirements.txt
/opt/loyalcorp/venv/bin/pip install -r /opt/loyalcorp/dialer/requirements.txt
```

## 4. Configuration
Edit `/opt/loyalcorp/bot/config.py`:
- `TELEGRAM_BOT_TOKEN` — your bot token from @BotFather
- `ADMIN_TELEGRAM_IDS` — your Telegram ID
- `OXAPAY_API_KEY` — from oxapay.com dashboard
- `DATABASE_CONFIG` — update password if changed
- `AMI_CONFIG` — Asterisk AMI credentials

## 5. Asterisk Setup
```bash
# Copy config files
cp asterisk/pjsip_loyalcorp.conf /etc/asterisk/
cp asterisk/extensions_loyalcorp.conf /etc/asterisk/

# Edit pjsip_loyalcorp.conf — fill in your SIP provider details:
#   YOUR_SIP_PROVIDER_HOST
#   YOUR_SIP_USERNAME
#   YOUR_SIP_PASSWORD

# Add includes to main Asterisk configs:
echo "#include pjsip_loyalcorp.conf" >> /etc/asterisk/pjsip.conf
echo "#include extensions_loyalcorp.conf" >> /etc/asterisk/extensions.conf

# Reload Asterisk
asterisk -rx "core reload"
asterisk -rx "pjsip reload"
asterisk -rx "dialplan reload"

# Verify endpoint loaded:
asterisk -rx "pjsip show endpoints"
# Should show: loyalcorp_trunk
```

## 6. AMI Setup
Add to `/etc/asterisk/manager.conf`:
```
[loyalcorp_bot]
secret = LoyalCorpAMI2026
permit = 127.0.0.1/255.255.255.0
read = all
write = originate,system,call,agent
```
Then: `asterisk -rx "manager reload"`

## 7. Systemd Services
```bash
cp systemd/loyalcorp-bot.service    /etc/systemd/system/
cp systemd/loyalcorp-worker.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable loyalcorp-bot loyalcorp-worker
systemctl start loyalcorp-bot loyalcorp-worker

# Check status:
systemctl status loyalcorp-bot
systemctl status loyalcorp-worker
```

## 8. Firewall
```bash
ufw allow 8000/tcp   # Webhook server (Oxapay + Asterisk callbacks)
ufw allow 5060/udp   # SIP
```

## 9. Bot Commands Reference

### Admin Commands
| Command | Description |
|---|---|
| `/approve <id>` | Approve a user |
| `/ban <id> [reason]` | Ban a user |
| `/unban <id>` | Unban a user |
| `/addbal <id> <amount>` | Add credits |
| `/removebal <id> <amount>` | Remove credits |

### User Commands
| Command | Description |
|---|---|
| `/start` | Main menu |
| `/setp1group` | Set P1 notification group (run in group) |
| `/unsetp1group` | Remove P1 group notification |

## 10. Admin Panel (in-bot)
- Pending Users — approve/ban new registrations
- All Users — view user list and stats
- Payments — confirm/view Oxapay payments
- Set Price/1k — change per-1000-numbers price

## 11. Campaign Flow
1. User registers → admin approves
2. User buys credits (Oxapay USDT)
3. User creates campaign: Name → Lead list → CPS → Intro sound → Outro sound → Confirm
4. Campaign deducts credits upfront (total_numbers / 1000 × price_per_1k)
5. Worker dials numbers via `loyalcorp_trunk` endpoint
6. Press-1 hits notify user via DM and optional group