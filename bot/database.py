# =============================================================================
# LoyalCorp P1 Bot — Database Layer
# =============================================================================

import asyncpg
import logging
from typing import Optional, List, Dict
from datetime import datetime

from config import DATABASE_URL, DATABASE_CONFIG

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    # =========================================================================
    # Connection
    # =========================================================================

    async def connect(self):
        schema = DATABASE_CONFIG.get('schema', 'public')
        self.pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=5, max_size=20,
            server_settings={'search_path': schema}
        )
        logger.info("✅ Database connected")

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("🔴 Database disconnected")

    # =========================================================================
    # Users
    # =========================================================================

    async def get_or_create_user(self, telegram_id: int, username: str = None,
                                  first_name: str = None, last_name: str = None) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1", telegram_id
            )
            if row:
                await conn.execute(
                    "UPDATE users SET last_active = NOW() WHERE telegram_id = $1", telegram_id
                )
                return dict(row)
            new = await conn.fetchrow("""
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES ($1, $2, $3, $4) RETURNING *
            """, telegram_id, username, first_name, last_name)
            return dict(new)

    async def get_user(self, telegram_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
            return dict(row) if row else None

    async def is_user_approved(self, telegram_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, is_banned FROM users WHERE telegram_id = $1", telegram_id
            )
            if not row:
                return False
            return row['is_active'] and not row['is_banned']

    async def approve_user(self, telegram_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_active = TRUE WHERE telegram_id = $1", telegram_id
            )
            return result.endswith("1")

    async def ban_user(self, telegram_id: int, reason: str = "") -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_banned = TRUE, ban_reason = $2 WHERE telegram_id = $1",
                telegram_id, reason
            )
            return result.endswith("1")

    async def unban_user(self, telegram_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE telegram_id = $1",
                telegram_id
            )
            return result.endswith("1")

    async def add_credits(self, telegram_id: int, amount: float) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE users SET credits = credits + $2, total_spent = total_spent + $2
                WHERE telegram_id = $1 RETURNING credits
            """, telegram_id, amount)
            return float(row['credits']) if row else 0.0

    async def remove_credits(self, telegram_id: int, amount: float) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE users SET credits = GREATEST(0, credits - $2)
                WHERE telegram_id = $1 RETURNING credits
            """, telegram_id, amount)
            return float(row['credits']) if row else 0.0

    async def deduct_credits(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET credits = GREATEST(0, credits - $2) WHERE id = $1",
                user_id, amount
            )

    async def get_all_users(self) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.*,
                    (SELECT COUNT(*) FROM campaigns WHERE user_id = u.id) as campaign_count,
                    (SELECT COUNT(*) FROM leads WHERE user_id = u.id) as lead_count,
                    (SELECT COUNT(*) FROM calls c JOIN campaigns ca ON c.campaign_id = ca.id WHERE ca.user_id = u.id) as total_calls,
                    (SELECT COUNT(*) FROM calls c JOIN campaigns ca ON c.campaign_id = ca.id WHERE ca.user_id = u.id AND c.dtmf_pressed = 1) as p1_count
                FROM users u ORDER BY u.created_at DESC
            """)
            return [dict(r) for r in rows]

    async def get_pending_users(self) -> List[dict]:
        """Users who registered but are not yet approved"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM users
                WHERE is_active = FALSE AND is_banned = FALSE
                ORDER BY created_at ASC
            """)
            return [dict(r) for r in rows]

    # =========================================================================
    # Settings
    # =========================================================================

    async def get_setting(self, key: str, default=None) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM bot_settings WHERE key = $1", key)
            return row['value'] if row else default

    async def set_setting(self, key: str, value: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
            """, key, value)

    async def get_price_per_1k(self) -> float:
        val = await self.get_setting('price_per_1k', '5.00')
        return float(val)

    async def set_price_per_1k(self, price: float):
        await self.set_setting('price_per_1k', str(round(price, 4)))

    # =========================================================================
    # Payments
    # =========================================================================

    async def create_payment(self, user_id: int, track_id: str, order_id: str,
                              amount: float, credits: float, payment_url: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO payments (user_id, track_id, order_id, amount, credits, payment_url)
                VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
            """, user_id, track_id, order_id, amount, credits, payment_url)
            return dict(row)

    async def get_payment_by_track_id(self, track_id: str) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT p.*, u.telegram_id FROM payments p
                JOIN users u ON u.id = p.user_id
                WHERE p.track_id = $1
            """, track_id)
            return dict(row) if row else None

    async def confirm_payment(self, track_id: str, tx_hash: str = "") -> bool:
        async with self.pool.acquire() as conn:
            payment = await conn.fetchrow(
                "SELECT * FROM payments WHERE track_id = $1 AND status = 'pending'", track_id
            )
            if not payment:
                return False
            await conn.execute("""
                UPDATE payments SET status = 'confirmed', tx_hash = $2, confirmed_at = NOW()
                WHERE track_id = $1
            """, track_id, tx_hash)
            await conn.execute("""
                UPDATE users SET credits = credits + $2
                WHERE id = $1
            """, payment['user_id'], payment['credits'])
            return True

    async def get_pending_payments(self) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT p.*, u.telegram_id, u.first_name, u.username
                FROM payments p JOIN users u ON u.id = p.user_id
                WHERE p.status = 'pending' ORDER BY p.created_at DESC LIMIT 20
            """)
            return [dict(r) for r in rows]

    # =========================================================================
    # Leads
    # =========================================================================

    async def create_lead(self, user_id: int, list_name: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO leads (user_id, list_name, updated_at) VALUES ($1, $2, NOW())
                ON CONFLICT (user_id, list_name) DO UPDATE SET updated_at = NOW()
                RETURNING *
            """, user_id, list_name)
            if not row:
                row = await conn.fetchrow(
                    "SELECT * FROM leads WHERE user_id=$1 AND list_name=$2", user_id, list_name
                )
            return dict(row)

    async def get_user_leads(self, user_id: int) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM leads WHERE user_id = $1 ORDER BY created_at DESC", user_id
            )
            return [dict(r) for r in rows]

    async def get_lead(self, lead_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
            return dict(row) if row else None

    async def bulk_insert_numbers(self, lead_id: int, numbers: List[str]) -> int:
        unique = list(dict.fromkeys(numbers))
        inserted = 0
        async with self.pool.acquire() as conn:
            for n in unique:
                result = await conn.execute("""
                    INSERT INTO lead_numbers (lead_id, phone_number)
                    VALUES ($1, $2) ON CONFLICT DO NOTHING
                """, lead_id, n)
                # result is like "INSERT 0 1" or "INSERT 0 0"
                if result.endswith("1"):
                    inserted += 1
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM lead_numbers WHERE lead_id = $1", lead_id
            )
            await conn.execute("""
                UPDATE leads SET total_numbers = $2, available_numbers = $2
                WHERE id = $1
            """, lead_id, total)
        return inserted

    async def delete_lead(self, lead_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM leads WHERE id = $1 AND user_id = $2", lead_id, user_id
            )
            return result.endswith("1")

    # =========================================================================
    # Campaigns
    # =========================================================================

    async def create_campaign(self, user_id: int, name: str, lead_id: int,
                               caller_id: str, cps: int, voice_file: str,
                               outro_file: str, cost_per_1k: float) -> dict:
        async with self.pool.acquire() as conn:
            # Get total numbers from lead
            total = await conn.fetchval(
                "SELECT total_numbers FROM leads WHERE id = $1", lead_id
            )
            row = await conn.fetchrow("""
                INSERT INTO campaigns
                    (user_id, lead_id, name, caller_id, cps, voice_file, outro_file,
                     cost_per_1k, total_numbers)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *
            """, user_id, lead_id, name, caller_id, cps, voice_file, outro_file,
                cost_per_1k, total or 0)
            return dict(row)

    async def get_user_campaigns(self, user_id: int, limit: int = 15) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.*, l.list_name as lead_name
                FROM campaigns c
                LEFT JOIN leads l ON c.lead_id = l.id
                WHERE c.user_id = $1 ORDER BY c.created_at DESC LIMIT $2
            """, user_id, limit)
            return [dict(r) for r in rows]

    async def get_campaign_stats(self, campaign_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT c.*, l.list_name as lead_name
                FROM campaigns c
                LEFT JOIN leads l ON c.lead_id = l.id
                WHERE c.id = $1
            """, campaign_id)
            return dict(row) if row else None

    async def start_campaign(self, campaign_id: int):
        async with self.pool.acquire() as conn:
            # Copy lead numbers to campaign_data
            await conn.execute("""
                INSERT INTO campaign_data (campaign_id, lead_number_id, phone_number)
                SELECT $1, ln.id, ln.phone_number
                FROM lead_numbers ln
                JOIN campaigns c ON c.lead_id = ln.lead_id
                WHERE c.id = $1
                AND ln.id NOT IN (
                    SELECT lead_number_id FROM campaign_data
                    WHERE campaign_id = $1 AND lead_number_id IS NOT NULL
                )
            """, campaign_id)
            await conn.execute("""
                UPDATE campaigns SET status = 'running', started_at = NOW()
                WHERE id = $1
            """, campaign_id)

    async def pause_campaign(self, campaign_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE campaigns SET status = 'paused' WHERE id = $1", campaign_id
            )

    async def stop_campaign(self, campaign_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE campaigns SET status = 'paused' WHERE id = $1", campaign_id
            )

    async def complete_campaign(self, campaign_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE campaigns SET status = 'completed', completed_at = NOW()
                WHERE id = $1
            """, campaign_id)

    async def delete_campaign(self, campaign_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE campaigns SET status = 'paused' WHERE id = $1", campaign_id
            )
            result = await conn.execute(
                "DELETE FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user_id
            )
            return result.endswith("1")

    async def reset_campaign(self, campaign_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM calls WHERE campaign_id = $1", campaign_id
            )
            await conn.execute(
                "DELETE FROM campaign_data WHERE campaign_id = $1", campaign_id
            )
            await conn.execute("""
                UPDATE campaigns SET
                    status = 'draft', completed = 0, answered = 0,
                    pressed_one = 0, failed = 0, actual_cost = 0,
                    started_at = NULL, completed_at = NULL
                WHERE id = $1
            """, campaign_id)

    async def get_running_campaigns(self) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.*, l.list_name as lead_name,
                       u.credits as user_credits, u.telegram_id
                FROM campaigns c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN leads l ON c.lead_id = l.id
                WHERE c.status = 'running'
                AND c.completed < c.total_numbers
                ORDER BY c.created_at ASC
            """)
            return [dict(r) for r in rows]

    async def get_pending_numbers(self, campaign_id: int, limit: int = 10) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (phone_number) id, phone_number
                FROM campaign_data
                WHERE campaign_id = $1
                AND status = 'pending'
                AND phone_number NOT IN (
                    SELECT phone_number FROM campaign_data
                    WHERE campaign_id = $1 AND status = 'dialing'
                )
                ORDER BY phone_number, id ASC
                LIMIT $2
            """, campaign_id, limit)
            return [dict(r) for r in rows]

    async def get_active_dialing_count(self, campaign_id: int) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT COUNT(*) FROM campaign_data
                WHERE campaign_id = $1 AND status = 'dialing'
            """, campaign_id) or 0

    async def get_call_logs(self, campaign_id: int, limit: int = 10) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT phone_number, status, dtmf_pressed, duration, cost
                FROM calls WHERE campaign_id = $1
                ORDER BY started_at DESC LIMIT $2
            """, campaign_id, limit)
            return [dict(r) for r in rows]

    async def get_p1_results(self, campaign_id: int) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT phone_number, duration, ended_at
                FROM calls WHERE campaign_id = $1 AND dtmf_pressed = 1
                ORDER BY ended_at DESC
            """, campaign_id)
            return [dict(r) for r in rows]

    async def get_full_report(self, campaign_id: int) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT phone_number, status, dtmf_pressed, duration, billsec, cost,
                       caller_id, started_at, ended_at
                FROM calls WHERE campaign_id = $1
                ORDER BY started_at ASC
            """, campaign_id)
            return [dict(r) for r in rows]

    async def get_user_stats(self, telegram_id: int) -> dict:
        async with self.pool.acquire() as conn:
            u = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
            if not u:
                return {}
            counts = await conn.fetchrow("""
                SELECT
                    COUNT(DISTINCT l.id) as lead_count,
                    COUNT(DISTINCT ca.id) as campaign_count,
                    COALESCE(SUM(ca.pressed_one), 0) as total_p1,
                    COALESCE(SUM(ca.answered), 0) as total_answered,
                    COALESCE(SUM(ca.completed), 0) as total_completed
                FROM users u
                LEFT JOIN leads l ON l.user_id = u.id
                LEFT JOIN campaigns ca ON ca.user_id = u.id
                WHERE u.telegram_id = $1
            """, telegram_id)
            return {
                'credits': float(u['credits']),
                'total_spent': float(u['total_spent']),
                'lead_count': counts['lead_count'],
                'campaign_count': counts['campaign_count'],
                'total_p1': counts['total_p1'],
                'total_answered': counts['total_answered'],
                'total_completed': counts['total_completed'],
            }


db = Database()