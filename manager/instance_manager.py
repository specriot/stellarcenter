import json
import os
import shutil
import asyncpg
from pathlib import Path

import db
import settings

# Standard Asterisk sound subdirs that every instance needs.
# Symlinked (not copied) to save disk space.
_ASTERISK_STD_SOUND_DIRS = ['en', 'digits', 'letters', 'phonetic', 'silence']


async def create_instance(req) -> dict:
    if db.token_exists(req.bot_token):
        raise ValueError("Bot token already registered to an existing instance")

    sip_port, ami_port, webhook_port = db.allocate_ports()

    instance_id = db.insert_instance({
        'name':             req.name or f"Instance {sip_port - 5059}",
        'bot_token':        req.bot_token,
        'sip_user':         req.sip_user,
        'sip_pass':         req.sip_pass,
        'sip_port':         sip_port,
        'ami_port':         ami_port,
        'webhook_port':     webhook_port,
        'db_schema':        f'bot_{sip_port - 5059}',  # temp, overwritten below
        'company_name':     req.company_name or 'LoyalCorp P1',
        'support_username': req.support_username or '@loyalcorpsupport',
        'admin_ids':        json.dumps(req.admin_ids or []),
        'instance_dir':     '',  # overwritten below
    })

    schema   = f'bot_{instance_id}'
    inst_dir = os.path.join(settings.INSTANCES_DIR, str(instance_id))

    db.patch_instance(instance_id, {
        'db_schema':    schema,
        'instance_dir': inst_dir,
    })

    _create_dirs(inst_dir)
    _write_config(instance_id, req, sip_port, ami_port, webhook_port, schema, inst_dir)
    await _create_pg_schema(schema)

    db.update_status(instance_id, 'created')
    return db.get_instance(instance_id)


def get_instance(instance_id: int) -> dict:
    inst = db.get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance {instance_id} not found")
    return inst


def list_instances() -> list:
    return db.list_instances()


def _create_dirs(inst_dir: str):
    for sub in ['asterisk', 'asterisk-data/sounds', 'logs', 'uploads']:
        Path(os.path.join(inst_dir, sub)).mkdir(parents=True, exist_ok=True)

    sounds_dst = os.path.join(inst_dir, 'asterisk-data', 'sounds')

    # Symlink standard Asterisk sound dirs so each instance's Asterisk
    # can find digits/prompts without duplicating hundreds of MB.
    for d in _ASTERISK_STD_SOUND_DIRS:
        src = os.path.join(settings.ASTERISK_SOUNDS_DIR, d)
        dst = os.path.join(sounds_dst, d)
        if os.path.exists(src) and not os.path.exists(dst):
            os.symlink(src, dst)

    # Copy existing custom IVR sounds as defaults for this instance.
    if os.path.isdir(settings.DEFAULT_SOUNDS_SRC):
        for f in os.listdir(settings.DEFAULT_SOUNDS_SRC):
            src_file = os.path.join(settings.DEFAULT_SOUNDS_SRC, f)
            if os.path.isfile(src_file):
                shutil.copy2(src_file, os.path.join(sounds_dst, f))


def _write_config(instance_id: int, req, sip_port: int, ami_port: int,
                  webhook_port: int, schema: str, inst_dir: str):
    config = {
        "instance_id": instance_id,

        "telegram_bot_token": req.bot_token,
        "admin_telegram_ids": req.admin_ids or [],

        "oxapay_api_key":     settings.OXAPAY_API_KEY,
        "oxapay_api_url":     settings.OXAPAY_API_URL,
        "oxapay_webhook_url": f"http://{settings.SERVER_IP}:{webhook_port}/webhook/oxapay",
        "default_currency":   "USDT",
        "min_topup_amount":   10,

        "default_price_per_1k": 5.0,

        "database": {
            "host":     settings.DB_HOST,
            "port":     settings.DB_PORT,
            "database": settings.DB_NAME,
            "user":     settings.DB_USER,
            "password": settings.DB_PASS,
            "schema":   schema,
        },

        "ami": {
            "host":     "127.0.0.1",
            "port":     ami_port,
            "username": settings.AMI_USERNAME,
            "secret":   settings.AMI_SECRET,
        },

        "sip_host": settings.MAGNUS_IP,
        "sip_user": req.sip_user,
        "sip_pass": req.sip_pass,
        "sip_port": sip_port,

        "pjsip_conf_path":     os.path.join(inst_dir, 'asterisk', 'pjsip.conf'),
        "pjsip_endpoint":      "loyalcorp_trunk",
        "ivr_context":         "loyalcorp-p1-ivr",
        "default_caller_id":   "12025551234",

        "asterisk_config_dir": os.path.join(inst_dir, 'asterisk'),
        "asterisk_data_dir":   os.path.join(inst_dir, 'asterisk-data'),
        "asterisk_sounds_dir": os.path.join(inst_dir, 'asterisk-data', 'sounds'),

        "max_concurrent_calls":   10,
        "call_timeout_seconds":   30,
        "delay_between_calls":    1,
        "country_code":           "1",

        "webhook_host": "0.0.0.0",
        "webhook_port": webhook_port,
        "webhook_url":  f"http://{settings.SERVER_IP}:{webhook_port}",

        "cost_per_minute":           1.0,
        "minimum_billable_seconds":  6,
        "billing_increment_seconds": 6,

        "log_level": "INFO",
        "log_file":  os.path.join(inst_dir, 'logs', 'bot.log'),

        "company_name":     req.company_name or "LoyalCorp P1",
        "support_username": req.support_username or "@loyalcorpsupport",

        "upload_dir": os.path.join(inst_dir, 'uploads'),
    }

    with open(os.path.join(inst_dir, 'config.json'), 'w') as f:
        json.dump(config, f, indent=2)


async def _create_pg_schema(schema: str):
    conn = await asyncpg.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASS,
        database=settings.DB_NAME,
    )
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await conn.close()

    conn = await asyncpg.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASS,
        database=settings.DB_NAME,
        server_settings={'search_path': schema},
    )
    with open(settings.SCHEMA_SQL) as f:
        await conn.execute(f.read())
    await conn.close()
