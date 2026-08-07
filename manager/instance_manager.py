import json
import os
import shutil
import subprocess
import asyncpg
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

import db
import settings

_ASTERISK_STD_SOUND_DIRS = ['en', 'digits', 'letters', 'phonetic', 'silence']

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
_jinja = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), keep_trailing_newline=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _systemctl(*args) -> bool:
    r = subprocess.run(['systemctl'] + list(args), capture_output=True, text=True)
    return r.returncode == 0


def _svc_names(instance_id: int) -> tuple:
    return (
        f'loyalcorp-asterisk-{instance_id}',
        f'loyalcorp-bot-{instance_id}',
        f'loyalcorp-worker-{instance_id}',
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

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
        'db_schema':        f'bot_{sip_port - 5059}',
        'company_name':     req.company_name or 'LoyalCorp P1',
        'support_username': req.support_username or '@loyalcorpsupport',
        'admin_ids':        json.dumps(req.admin_ids or []),
        'instance_dir':     '',
    })

    schema   = f'bot_{instance_id}'
    inst_dir = os.path.join(settings.INSTANCES_DIR, str(instance_id))
    name     = req.name or f"Instance {instance_id}"

    db.patch_instance(instance_id, {
        'db_schema':    schema,
        'instance_dir': inst_dir,
    })

    _create_dirs(inst_dir)
    _write_config(instance_id, req, sip_port, ami_port, webhook_port, schema, inst_dir)
    _generate_asterisk_configs(instance_id, req, sip_port, ami_port, inst_dir)
    _generate_systemd_units(instance_id, name, inst_dir)
    await _create_pg_schema(schema)

    db.update_status(instance_id, 'created')
    return db.get_instance(instance_id)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_instance(instance_id: int) -> dict:
    inst = db.get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance {instance_id} not found")
    inst['services'] = get_service_status(instance_id)
    return inst


def list_instances() -> list:
    rows = db.list_instances()
    for row in rows:
        row['services'] = get_service_status(row['id'])
    return rows


def get_service_status(instance_id: int) -> dict:
    svc_ast, svc_bot, svc_wrk = _svc_names(instance_id)
    status = {}
    for key, svc in [('asterisk', svc_ast), ('bot', svc_bot), ('worker', svc_wrk)]:
        r = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True)
        status[key] = r.stdout.strip()
    return status


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------

def start_instance(instance_id: int):
    svc_ast, svc_bot, svc_wrk = _svc_names(instance_id)
    _systemctl('start', svc_ast)
    import time; time.sleep(3)  # give Asterisk a moment before bot/worker connect to AMI
    _systemctl('start', svc_bot)
    _systemctl('start', svc_wrk)
    db.update_status(instance_id, 'running')


def stop_instance(instance_id: int):
    _, svc_bot, svc_wrk = _svc_names(instance_id)
    _systemctl('stop', svc_bot)
    _systemctl('stop', svc_wrk)
    db.update_status(instance_id, 'stopped')


def pause_instance(instance_id: int):
    _, _, svc_wrk = _svc_names(instance_id)
    _systemctl('stop', svc_wrk)
    db.update_status(instance_id, 'paused')


def resume_instance(instance_id: int):
    _, _, svc_wrk = _svc_names(instance_id)
    _systemctl('start', svc_wrk)
    db.update_status(instance_id, 'running')


async def delete_instance(instance_id: int):
    inst = db.get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance {instance_id} not found")

    svc_ast, svc_bot, svc_wrk = _svc_names(instance_id)
    for svc in (svc_bot, svc_wrk, svc_ast):
        _systemctl('stop', svc)
        _systemctl('disable', svc)
        path = f'/etc/systemd/system/{svc}.service'
        if os.path.exists(path) or os.path.islink(path):
            os.remove(path)
    _systemctl('daemon-reload')

    inst_dir = inst.get('instance_dir', '')
    if inst_dir and os.path.isdir(inst_dir):
        shutil.rmtree(inst_dir)

    schema = inst.get('db_schema', '')
    if schema:
        try:
            conn = await asyncpg.connect(
                host=settings.DB_HOST, port=settings.DB_PORT,
                user=settings.DB_USER, password=settings.DB_PASS,
                database=settings.DB_NAME,
            )
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.close()
        except Exception as e:
            pass  # log-worthy but not fatal

    db.delete_instance(instance_id)


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _create_dirs(inst_dir: str):
    for sub in ['asterisk', 'asterisk-data/sounds', 'asterisk-data/spool',
                'logs', 'uploads', 'run', 'systemd']:
        Path(os.path.join(inst_dir, sub)).mkdir(parents=True, exist_ok=True)

    sounds_dst = os.path.join(inst_dir, 'asterisk-data', 'sounds')

    for d in _ASTERISK_STD_SOUND_DIRS:
        src = os.path.join(settings.ASTERISK_SOUNDS_DIR, d)
        dst = os.path.join(sounds_dst, d)
        if os.path.exists(src) and not os.path.exists(dst):
            os.symlink(src, dst)

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


def _generate_asterisk_configs(instance_id: int, req, sip_port: int,
                               ami_port: int, inst_dir: str):
    ctx = {
        'instance_id':  instance_id,
        'inst_dir':     inst_dir,
        'sip_port':     sip_port,
        'sip_user':     req.sip_user,
        'sip_pass':     req.sip_pass,
        'magnus_ip':    settings.MAGNUS_IP,
        'ami_port':     ami_port,
        'ami_username': settings.AMI_USERNAME,
        'ami_secret':   settings.AMI_SECRET,
    }

    asterisk_dir = os.path.join(inst_dir, 'asterisk')

    for tpl, out in [
        ('asterisk.conf.j2', 'asterisk.conf'),
        ('pjsip.conf.j2',    'pjsip.conf'),
        ('manager.conf.j2',  'manager.conf'),
        ('logger.conf.j2',   'logger.conf'),
    ]:
        content = _jinja.get_template(tpl).render(**ctx)
        with open(os.path.join(asterisk_dir, out), 'w') as f:
            f.write(content)

    ext_src = os.path.join(settings.SOURCE_DIR, 'asterisk', 'extensions_loyalcorp.conf')
    shutil.copy2(ext_src, os.path.join(asterisk_dir, 'extensions.conf'))

    for static in ['amd.conf', 'modules.conf']:
        src = os.path.join('/etc/asterisk', static)
        dst = os.path.join(asterisk_dir, static)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)


def _generate_systemd_units(instance_id: int, name: str, inst_dir: str):
    ctx = {
        'instance_id': instance_id,
        'name':        name,
        'inst_dir':    inst_dir,
    }

    systemd_dir = os.path.join(inst_dir, 'systemd')

    for tpl, svc in [
        ('asterisk.service.j2', f'loyalcorp-asterisk-{instance_id}.service'),
        ('bot.service.j2',      f'loyalcorp-bot-{instance_id}.service'),
        ('worker.service.j2',   f'loyalcorp-worker-{instance_id}.service'),
    ]:
        content = _jinja.get_template(tpl).render(**ctx)
        local_path = os.path.join(systemd_dir, svc)
        with open(local_path, 'w') as f:
            f.write(content)

        system_path = f'/etc/systemd/system/{svc}'
        if os.path.exists(system_path) or os.path.islink(system_path):
            os.remove(system_path)
        os.symlink(local_path, system_path)

    _systemctl('daemon-reload')


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
