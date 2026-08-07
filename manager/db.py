import sqlite3
from contextlib import contextmanager
from typing import Optional
import settings


def init():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instances (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT,
                bot_token        TEXT NOT NULL UNIQUE,
                sip_user         TEXT NOT NULL,
                sip_pass         TEXT NOT NULL,
                sip_port         INTEGER NOT NULL UNIQUE,
                ami_port         INTEGER NOT NULL UNIQUE,
                webhook_port     INTEGER NOT NULL UNIQUE,
                db_schema        TEXT NOT NULL UNIQUE,
                company_name     TEXT DEFAULT 'LoyalCorp P1',
                support_username TEXT DEFAULT '@loyalcorpsupport',
                admin_ids        TEXT DEFAULT '[]',
                instance_dir     TEXT NOT NULL,
                status           TEXT DEFAULT 'created',
                created_at       TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


@contextmanager
def _conn():
    conn = sqlite3.connect(settings.SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def allocate_ports() -> tuple:
    with _conn() as conn:
        used_sip     = {r[0] for r in conn.execute("SELECT sip_port FROM instances")}
        used_ami     = {r[0] for r in conn.execute("SELECT ami_port FROM instances")}
        used_webhook = {r[0] for r in conn.execute("SELECT webhook_port FROM instances")}

    sip     = next(p for p in range(5060, 5260) if p not in used_sip)
    ami     = next(p for p in range(5038, 5238) if p not in used_ami)
    webhook = next(p for p in range(8000, 8200) if p not in used_webhook)
    return sip, ami, webhook


def insert_instance(data: dict) -> int:
    with _conn() as conn:
        cur = conn.execute("""
            INSERT INTO instances
                (name, bot_token, sip_user, sip_pass, sip_port, ami_port,
                 webhook_port, db_schema, company_name, support_username,
                 admin_ids, instance_dir)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data['name'],             data['bot_token'],
            data['sip_user'],         data['sip_pass'],
            data['sip_port'],         data['ami_port'],
            data['webhook_port'],     data['db_schema'],
            data['company_name'],     data['support_username'],
            data['admin_ids'],        data['instance_dir'],
        ))
        conn.commit()
        return cur.lastrowid


def patch_instance(instance_id: int, fields: dict):
    if not fields:
        return
    cols   = ', '.join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [instance_id]
    with _conn() as conn:
        conn.execute(f"UPDATE instances SET {cols} WHERE id=?", values)
        conn.commit()


def update_status(instance_id: int, status: str):
    patch_instance(instance_id, {'status': status})


def get_instance(instance_id: int) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return dict(row) if row else None


def list_instances() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM instances ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_instance(instance_id: int):
    with _conn() as conn:
        conn.execute("DELETE FROM instances WHERE id=?", (instance_id,))
        conn.commit()


def token_exists(bot_token: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM instances WHERE bot_token=?", (bot_token,)
        ).fetchone()
        return row is not None
