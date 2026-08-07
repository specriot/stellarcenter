import os
import json


def _load() -> dict:
    path = os.environ.get(
        'MANAGER_CONFIG_PATH',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manager.json')
    )
    with open(path) as f:
        return json.load(f)


_cfg = _load()

SERVER_IP      = _cfg['server_ip']
MAGNUS_IP      = _cfg['magnus_ip']

DB_HOST        = _cfg.get('db_host', 'localhost')
DB_PORT        = _cfg.get('db_port', 5432)
DB_NAME        = _cfg.get('db_name', 'loyalcorp_p1')
DB_USER        = _cfg['db_user']
DB_PASS        = _cfg['db_pass']

AMI_USERNAME   = _cfg.get('ami_username', 'loyalcorp_bot')
AMI_SECRET     = _cfg['ami_secret']

OXAPAY_API_KEY = _cfg['oxapay_api_key']
OXAPAY_API_URL = _cfg.get('oxapay_api_url', 'https://api.oxapay.com/v1/payment/invoice')

INSTANCES_DIR      = _cfg.get('instances_dir', '/opt/loyalcorp-instances')
SOURCE_DIR         = _cfg.get('source_dir', '/opt/loyalcorp')
SCHEMA_SQL         = os.path.join(SOURCE_DIR, 'database', 'schema.sql')
DEFAULT_SOUNDS_SRC = _cfg.get('default_sounds_src', '/var/lib/asterisk/sounds/custom')
ASTERISK_SOUNDS_DIR = _cfg.get('asterisk_sounds_dir', '/var/lib/asterisk/sounds')

API_KEY        = _cfg.get('api_key', '')

MANAGER_DIR    = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH    = os.path.join(MANAGER_DIR, 'manager.db')
