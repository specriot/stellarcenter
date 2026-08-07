# =============================================================================
# LoyalCorp P1 Bot — Campaign Worker
# Single global PJSIP trunk: loyalcorp_trunk
# =============================================================================

import asyncio
import logging
import uuid
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime
import asyncpg
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bot'))

from config import (
    DATABASE_URL, DATABASE_CONFIG, AMI_CONFIG, IVR_CONTEXT,
    PJSIP_ENDPOINT, MAX_CONCURRENT_CALLS,
    CALL_TIMEOUT_SECONDS, DELAY_BETWEEN_CALLS,
    COUNTRY_CODE, TELEGRAM_BOT_TOKEN, WEBHOOK_PORT
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AsteriskAMI:
    """Minimal Asterisk AMI client for Originate"""

    def __init__(self, host, port, username, secret):
        self.host     = host
        self.port     = port
        self.username = username
        self.secret   = secret
        self.reader   = None
        self.writer   = None
        self._lock    = asyncio.Lock()

    async def connect(self) -> bool:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=10
            )
            banner = await self.reader.readline()
            logger.info(f"AMI banner: {banner.decode().strip()}")
            await self._send(f"Action: Login\r\nUsername: {self.username}\r\nSecret: {self.secret}\r\n\r\n")
            resp = await self._read_response()
            if 'Success' in resp:
                logger.info("✅ AMI logged in")
                return True
            logger.error(f"AMI login failed: {resp}")
            return False
        except Exception as e:
            logger.error(f"AMI connect error: {e}")
            return False

    async def disconnect(self):
        if self.writer:
            try:
                await self._send("Action: Logoff\r\n\r\n")
                self.writer.close()
            except Exception:
                pass

    async def originate(self, phone: str, caller_id: str, context: str,
                        exten: str, voice_file: str, outro_file: str,
                        campaign_id: int, campaign_data_id: int, call_id: str) -> bool:
        variables = (
            f"CALL_ID={call_id},"
            f"CAMPAIGN_ID={campaign_id},"
            f"CAMPAIGN_DATA_ID={campaign_data_id},"
            f"VOICE_FILE={voice_file},"
            f"OUTRO_FILE={outro_file or ''},"
            f"WEBHOOK_URL=http://127.0.0.1:{WEBHOOK_PORT}"
        )
        cmd = (
            f"Action: Originate\r\n"
            f"Channel: PJSIP/{phone}@{PJSIP_ENDPOINT}\r\n"
            f"Context: {context}\r\n"
            f"Exten: {exten}\r\n"
            f"Priority: 1\r\n"
            f"CallerID: <{caller_id}>\r\n"
            f"Timeout: {CALL_TIMEOUT_SECONDS * 1000}\r\n"
            f"Variable: {variables}\r\n"
            f"ActionID: {call_id}\r\n"
            f"Async: true\r\n"
            f"Codecs: ulaw,alaw\r\n"
            f"\r\n"
        )
        for attempt in range(3):
            try:
                async with self._lock:
                    await self._send(cmd)
                logger.info(f"📞 Originated: {phone} via {PJSIP_ENDPOINT}")
                return True
            except Exception as e:
                logger.warning(f"Originate error (attempt {attempt+1}): {e} — reconnecting AMI")
                try:
                    await self.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(1)
                connected = await self.connect()
                if not connected:
                    logger.error("AMI reconnect failed")
                    return False
        logger.error(f"Originate failed after 3 attempts: {phone}")
        return False

    async def _send(self, text: str):
        self.writer.write(text.encode())
        await self.writer.drain()

    async def _read_response(self, timeout: float = 3.0) -> str:
        lines = []
        try:
            while True:
                line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
                decoded = line.decode(errors='ignore').strip()
                if not decoded:
                    break
                lines.append(decoded)
        except asyncio.TimeoutError:
            pass
        return '\n'.join(lines)


class CampaignWorker:
    def __init__(self):
        self.ami       = AsteriskAMI(**AMI_CONFIG)
        self.db_pool   = None
        self.running   = False
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    async def start(self):
        logger.info("🚀 LoyalCorp Campaign Worker starting...")
        schema = DATABASE_CONFIG.get('schema', 'public')
        self.db_pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=5, max_size=20,
            server_settings={'search_path': schema}
        )
        logger.info("✅ DB connected")

        connected = await self.ami.connect()
        if not connected:
            logger.error("❌ AMI connection failed")
            return False

        self.running = True
        logger.info(f"✅ Worker started — using endpoint: {PJSIP_ENDPOINT}")
        await self._loop()

    async def stop(self):
        self.running = False
        await self.ami.disconnect()
        if self.db_pool:
            await self.db_pool.close()

    async def _loop(self):
        while self.running:
            try:
                campaigns = await self._get_running_campaigns()
                if campaigns:
                    # Fire all campaigns in parallel — no awaiting each other
                    tasks = [
                        asyncio.create_task(self._process(c))
                        for c in campaigns
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    logger.info("💤 No running campaigns")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _get_running_campaigns(self) -> List[Dict]:
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.id, c.user_id, c.name, c.caller_id,
                       c.cps, c.voice_file, c.outro_file,
                       c.total_numbers, c.completed,
                       u.credits as user_credits, u.telegram_id
                FROM campaigns c
                JOIN users u ON c.user_id = u.id
                WHERE c.status = 'running'
                AND c.completed < c.total_numbers
                ORDER BY c.created_at ASC
            """)
            return [dict(r) for r in rows]

    async def _process(self, campaign: Dict):
        campaign_id  = campaign['id']
        cps          = campaign.get('cps', MAX_CONCURRENT_CALLS)
        caller_id    = campaign.get('caller_id') or ''
        voice_file   = campaign.get('voice_file', 'tt-weasels')
        outro_file   = campaign.get('outro_file', '')
        user_credits = float(campaign.get('user_credits', 0))

        # Check credits
        if user_credits <= 0:
            logger.warning(f"⚠️ Campaign {campaign_id}: No credits, pausing")
            await self._pause(campaign_id, "Insufficient credits")
            return

        active_dialing = await self._active_count(campaign_id)
        slots = cps - active_dialing
        if slots <= 0:
            return

        numbers = await self._pending_numbers(campaign_id, limit=slots)
        if not numbers:
            if active_dialing > 0:
                return
            # Only complete if we actually dialed at least some numbers
            total = campaign.get('total_numbers', 0)
            done  = campaign.get('completed', 0)
            if total > 0 and done > 0:
                await self._complete(campaign_id, campaign)
            elif total == 0:
                await self._pause(campaign_id, "No numbers in campaign")
            return

        logger.info(f"📞 Campaign {campaign_id}: dialing {len(numbers)} numbers (fire-and-forget)")
        for num in numbers:
            phone = num['phone_number']
            if not phone.startswith(COUNTRY_CODE):
                phone = COUNTRY_CODE + phone
            # Fire-and-forget: do not await, do not block the loop
            asyncio.create_task(
                self._dial(campaign_id, num['id'], phone, caller_id, voice_file, outro_file)
            )

    async def _dial(self, campaign_id: int, cd_id: int, phone: str,
                    caller_id: str, voice_file: str, outro_file: str):
        async with self.semaphore:
            call_id = str(uuid.uuid4())
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE campaign_data SET status='dialing', call_id=$1, called_at=NOW() WHERE id=$2",
                        call_id, cd_id
                    )
                    await conn.execute("""
                        INSERT INTO calls (campaign_id, campaign_data_id, call_id, phone_number, caller_id, started_at)
                        VALUES ($1,$2,$3,$4,$5,NOW())
                        ON CONFLICT (call_id) DO NOTHING
                    """, campaign_id, cd_id, call_id, phone, caller_id)

                success = await self.ami.originate(
                    phone=phone,
                    caller_id=caller_id,
                    context=IVR_CONTEXT,
                    exten=phone,
                    voice_file=voice_file,
                    outro_file=outro_file,
                    campaign_id=campaign_id,
                    campaign_data_id=cd_id,
                    call_id=call_id,
                )

                if not success:
                    async with self.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE campaign_data SET status='failed' WHERE id=$1", cd_id
                        )
                        await conn.execute(
                            "UPDATE calls SET status='FAILED', ended_at=NOW() WHERE call_id=$1", call_id
                        )
            except Exception as e:
                logger.error(f"Dial error ({phone}): {e}")
                try:
                    async with self.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE campaign_data SET status='failed' WHERE id=$1", cd_id
                        )
                except Exception:
                    pass

    async def _pending_numbers(self, campaign_id: int, limit: int) -> List[Dict]:
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (phone_number) id, phone_number
                FROM campaign_data
                WHERE campaign_id=$1 AND status='pending'
                AND phone_number NOT IN (
                    SELECT phone_number FROM campaign_data
                    WHERE campaign_id=$1 AND status='dialing'
                )
                ORDER BY phone_number, id ASC
                LIMIT $2
            """, campaign_id, limit)
            return [dict(r) for r in rows]

    async def _active_count(self, campaign_id: int) -> int:
        async with self.db_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM campaign_data WHERE campaign_id=$1 AND status='dialing'",
                campaign_id
            ) or 0

    async def _pause(self, campaign_id: int, reason: str = ""):
        async with self.db_pool.acquire() as conn:
            await conn.execute("UPDATE campaigns SET status='paused' WHERE id=$1", campaign_id)
        logger.info(f"⏸ Campaign {campaign_id} paused: {reason}")

    async def _complete(self, campaign_id: int, campaign: Dict):
        async with self.db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE campaigns SET status='completed', completed_at=NOW() WHERE id=$1
            """, campaign_id)
        logger.info(f"✅ Campaign {campaign_id} completed")
        await self._send_completion_notification(campaign_id, campaign)

    async def _send_completion_notification(self, campaign_id: int, campaign: Dict):
        telegram_id = campaign.get('telegram_id')
        if not telegram_id:
            return
        try:
            async with self.db_pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT total_numbers, completed, answered, pressed_one, failed,
                           COALESCE(actual_cost, 0) as actual_cost, name
                    FROM campaigns WHERE id = $1
                """, campaign_id)
                duration_row = await conn.fetchrow("""
                    SELECT COALESCE(SUM(duration), 0) as total_duration
                    FROM calls WHERE campaign_id = $1
                """, campaign_id)
                vm_row = await conn.fetchrow("""
                    SELECT COUNT(*) as vm_count
                    FROM calls WHERE campaign_id = $1 AND status = 'VOICEMAIL'
                """, campaign_id)
                noanswer_row = await conn.fetchrow("""
                    SELECT COUNT(*) as noanswer_count
                    FROM calls WHERE campaign_id = $1 AND status IN ('NO ANSWER', 'NOANSWER')
                """, campaign_id)

            if not stats:
                return

            dialed   = stats['completed'] or 0
            answered = stats['answered'] or 0
            p1s      = stats['pressed_one'] or 0
            vm       = int(vm_row['vm_count']) if vm_row else 0
            noanswer = int(noanswer_row['noanswer_count']) if noanswer_row else 0
            duration = int(duration_row['total_duration']) if duration_row else 0
            name     = stats['name'] or f"Campaign {campaign_id}"

            p1_rate = f"{(p1s / dialed * 100):.1f}%" if dialed > 0 else "0.0%"
            vm_rate = f"{(vm / dialed * 100):.1f}%" if dialed > 0 else "0.0%"

            mins, secs = divmod(duration, 60)
            hrs, mins  = divmod(mins, 60)
            dur_str    = f"{hrs}h {mins}m {secs}s" if hrs > 0 else f"{mins}m {secs}s"

            msg = (
                f"✅ <b>Campaign Completed</b>\n\n"
                f"📝 <b>{name}</b>\n\n"
                f"📊 <b>Results:</b>\n"
                f"  📞 P1s:        <b>{p1s}</b>\n"
                f"  ☎️ Dialed:     <b>{dialed}</b>\n"
                f"  ✅ Answered:   <b>{answered}</b>\n"
                f"  📬 Voicemail:  <b>{vm}</b>\n"
                f"  🔕 No Answer:  <b>{noanswer}</b>\n\n"
                f"  📈 P1 Rate:    <b>{p1_rate}</b>\n"
                f"  📬 VM Rate:    <b>{vm_rate}</b>\n"
                f"  ⏱ Duration:   <b>{dur_str}</b>"
            )

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id":    telegram_id,
                    "text":       msg,
                    "parse_mode": "HTML"
                })
            logger.info(f"📨 Completion notification sent to {telegram_id} for campaign {campaign_id}")
        except Exception as e:
            logger.error(f"Completion notification error: {e}")


async def main():
    worker = CampaignWorker()
    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())