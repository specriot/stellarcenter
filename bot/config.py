# =============================================================================
# LoyalCorp P1 Bot — Configuration
# =============================================================================

import os

# =============================================================================
# Telegram Bot
# =============================================================================
TELEGRAM_BOT_TOKEN = "8711753348:AAEE200_jh-Lo5ZkpTOYnspizojB94eL8xM"

# =============================================================================
# Admin Telegram IDs
# =============================================================================
ADMIN_TELEGRAM_IDS = [851025625, 6199609876, 7942873034,326854865]

# =============================================================================
# Oxapay Payment Gateway
# =============================================================================
OXAPAY_API_KEY    = "MYHHAL-ZLHOE9-5BRRGL-TEVEH5"
OXAPAY_API_URL    = "https://api.oxapay.com/v1/payment/invoice"
OXAPAY_WEBHOOK_URL = "http://85.11.167.108:8000/webhook/oxapay"

DEFAULT_CURRENCY  = "USDT"
MIN_TOPUP_AMOUNT  = 10   # Minimum $10 top-up

# =============================================================================
# Pricing — Per 1k Lines
# =============================================================================
# Admin can change this via bot panel at runtime
DEFAULT_PRICE_PER_1K = 5.0   # $5 per 1000 numbers (default)

# =============================================================================
# Database
# =============================================================================
DATABASE_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "loyalcorp_p1",
    "user": "loyalcorp",
    "password": "loyalcorp2026",
}
DATABASE_URL = (
    f"postgresql://{DATABASE_CONFIG['user']}:{DATABASE_CONFIG['password']}"
    f"@{DATABASE_CONFIG['host']}:{DATABASE_CONFIG['port']}/{DATABASE_CONFIG['database']}"
)

# =============================================================================
# Asterisk / SIP — Single Global Trunk
# =============================================================================
AMI_CONFIG = {
    "host": "127.0.0.1",
    "port": 5038,
    "username": "loyalcorp_bot",
    "secret":   "LoyalCorpAMI2026",
}

# Single global PJSIP endpoint — defined in /etc/asterisk/pjsip_loyalcorp.conf
PJSIP_ENDPOINT    = "loyalcorp_trunk"
IVR_CONTEXT       = "loyalcorp-p1-ivr"
DEFAULT_CALLER_ID = "12025551234"

# Asterisk sounds directory (default Asterisk path)
ASTERISK_SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

# =============================================================================
# Campaign Defaults
# =============================================================================
MAX_CONCURRENT_CALLS   = 10
CALL_TIMEOUT_SECONDS   = 30
DELAY_BETWEEN_CALLS    = 1   # seconds between each originate
COUNTRY_CODE           = "1"  # USA only

# =============================================================================
# Webhook Server
# =============================================================================
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8000
WEBHOOK_URL  = "http://85.11.167.108:8000"

# =============================================================================
# Billing
# =============================================================================
COST_PER_MINUTE           = 1.0   # $1/min
MINIMUM_BILLABLE_SECONDS  = 6
BILLING_INCREMENT_SECONDS = 6

# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL  = "INFO"
LOG_FILE   = "loyalcorp.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# =============================================================================
# Company
# =============================================================================
COMPANY_NAME     = "LoyalCorp P1"
SUPPORT_USERNAME = "@loyalcorpsupport"

# =============================================================================
# File Paths
# =============================================================================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
