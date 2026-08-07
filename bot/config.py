import os
import json


def _load() -> dict:
    path = os.environ.get('BOT_CONFIG_PATH')
    if not path:
        raise RuntimeError(
            "BOT_CONFIG_PATH is not set. "
            "Example: export BOT_CONFIG_PATH=/opt/loyalcorp-instances/1/config.json"
        )
    with open(path) as f:
        return json.load(f)


_cfg = _load()

# Telegram
TELEGRAM_BOT_TOKEN = _cfg['telegram_bot_token']
ADMIN_TELEGRAM_IDS = _cfg['admin_telegram_ids']

# Oxapay
OXAPAY_API_KEY     = _cfg['oxapay_api_key']
OXAPAY_API_URL     = _cfg.get('oxapay_api_url', 'https://api.oxapay.com/v1/payment/invoice')
OXAPAY_WEBHOOK_URL = _cfg['oxapay_webhook_url']
DEFAULT_CURRENCY   = _cfg.get('default_currency', 'USDT')
MIN_TOPUP_AMOUNT   = _cfg.get('min_topup_amount', 10)

# Pricing
DEFAULT_PRICE_PER_1K = _cfg.get('default_price_per_1k', 5.0)

# Database
DATABASE_CONFIG = _cfg['database']
DATABASE_URL = (
    f"postgresql://{DATABASE_CONFIG['user']}:{DATABASE_CONFIG['password']}"
    f"@{DATABASE_CONFIG['host']}:{DATABASE_CONFIG['port']}/{DATABASE_CONFIG['database']}"
)

# AMI
AMI_CONFIG = dict(_cfg['ami'])

# Asterisk
PJSIP_CONF_PATH   = _cfg.get('pjsip_conf_path', '/etc/asterisk/pjsip_loyalcorp.conf')
PJSIP_ENDPOINT    = _cfg.get('pjsip_endpoint', 'loyalcorp_trunk')
IVR_CONTEXT       = _cfg.get('ivr_context', 'loyalcorp-p1-ivr')
DEFAULT_CALLER_ID = _cfg.get('default_caller_id', '12025551234')
ASTERISK_SOUNDS_DIR = _cfg['asterisk_sounds_dir']

# Campaign defaults
MAX_CONCURRENT_CALLS   = _cfg.get('max_concurrent_calls', 10)
CALL_TIMEOUT_SECONDS   = _cfg.get('call_timeout_seconds', 30)
DELAY_BETWEEN_CALLS    = _cfg.get('delay_between_calls', 1)
COUNTRY_CODE           = _cfg.get('country_code', '1')

# Webhook server
WEBHOOK_HOST = _cfg.get('webhook_host', '0.0.0.0')
WEBHOOK_PORT = _cfg['webhook_port']
WEBHOOK_URL  = _cfg['webhook_url']

# Billing
COST_PER_MINUTE           = _cfg.get('cost_per_minute', 1.0)
MINIMUM_BILLABLE_SECONDS  = _cfg.get('minimum_billable_seconds', 6)
BILLING_INCREMENT_SECONDS = _cfg.get('billing_increment_seconds', 6)

# Logging
LOG_LEVEL  = _cfg.get('log_level', 'INFO')
LOG_FILE   = _cfg.get('log_file', 'loyalcorp.log')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Company
COMPANY_NAME     = _cfg.get('company_name', 'LoyalCorp P1')
SUPPORT_USERNAME = _cfg.get('support_username', '@loyalcorpsupport')

# File paths
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = _cfg.get('upload_dir', os.path.join(BASE_DIR, 'uploads'))
os.makedirs(UPLOAD_DIR, exist_ok=True)
