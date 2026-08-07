# =============================================================================
# LoyalCorp P1 Bot — Webhook Server (Oxapay + Asterisk callbacks)
# =============================================================================

import logging
import json
import math
from aiohttp import web
from datetime import datetime

logger = logging.getLogger(__name__)


class WebhookServer:
    def __init__(self, db, bot_app=None, host="0.0.0.0", port=8000):
        self.db      = db
        self.bot_app = bot_app
        self.host    = host
        self.port    = port
        self.runner  = None

    async def start(self):
        app = web.Application()
        app.router.add_post('/webhook/oxapay', self.handle_oxapay)
        app.router.add_post('/webhook/dtmf',   self.handle_dtmf)
        app.router.add_post('/webhook/hangup', self.handle_hangup)
        app.router.add_get('/health',          self.handle_health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info(f"🌐 Webhook server started on {self.host}:{self.port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def handle_health(self, request):
        return web.json_response({"status": "ok", "time": datetime.now().isoformat()})

    # =========================================================================
    # Oxapay
    # =========================================================================

    async def handle_oxapay(self, request):
        try:
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)

            logger.info(f"📨 Oxapay webhook: {json.dumps(data, default=str)}")
            track_id = data.get('trackId') or data.get('track_id') or data.get('orderId')
            status   = data.get('status', '').lower()
            tx_hash  = data.get('txID') or data.get('tx_hash', '')

            if not track_id:
                return web.json_response({"error": "No trackId"}, status=400)

            if status in ('paid', 'complete', 'completed', 'confirmed'):
                await self._handle_paid(track_id, tx_hash)
            elif status in ('failed', 'expired', 'canceled'):
                logger.info(f"❌ Payment {track_id} {status}")

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Oxapay webhook error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_paid(self, track_id: str, tx_hash: str = ""):
        confirmed = await self.db.confirm_payment(track_id, tx_hash)
        if confirmed:
            payment = await self.db.get_payment_by_track_id(track_id)
            if payment:
                await self._notify(
                    payment['telegram_id'],
                    f"✅ <b>Payment Confirmed!</b>\n\n"
                    f"💰 <b>${payment['credits']:.2f}</b> credits added to your account.\n"
                    f"Use /start to see your balance. 🎉"
                )
                logger.info(f"✅ Payment confirmed: {track_id} +${payment['credits']:.2f}")
        else:
            logger.warning(f"⚠️ Payment {track_id} not found or already confirmed")

    # =========================================================================
    # Asterisk DTMF webhook
    # =========================================================================

    async def handle_dtmf(self, request):
        try:
            content_type = request.content_type or ''
            data = await request.json() if 'json' in content_type else dict(await request.post())
            logger.info(f"📞 DTMF webhook: {data}")

            call_id          = data.get('call_id', '')
            digit            = data.get('digit', '')
            duration         = int(data.get('duration') or 0)
            campaign_id      = data.get('campaign_id', '')
            campaign_data_id = data.get('campaign_data_id', '')
            amd_status       = data.get('amd_status', '')

            if not call_id:
                return web.json_response({"error": "No call_id"}, status=400)

            dtmf_pressed = 1 if digit == '1' else 0

            if amd_status == 'MACHINE':
                call_status = 'MACHINE'
                cost = 0
            else:
                cost = self._calc_cost(duration)
                if dtmf_pressed:
                    call_status = 'COMPLETED'
                elif duration > 0:
                    call_status = 'ANSWER'
                else:
                    call_status = 'NO ANSWER'

            async with self.db.pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE calls SET dtmf_pressed=$1, duration=$2, status=$3, cost=$4,
                        ended_at=NOW()
                    WHERE call_id=$5
                """, dtmf_pressed, duration, call_status, cost, call_id)

                rows_updated = int(result.split()[-1])
                if rows_updated == 0 and campaign_data_id:
                    try:
                        await conn.execute("""
                            UPDATE calls SET dtmf_pressed=$1, duration=$2, status=$3, cost=$4,
                                ended_at=NOW()
                            WHERE campaign_data_id=$5
                        """, dtmf_pressed, duration, call_status, cost, int(campaign_data_id))
                    except Exception:
                        pass

                if campaign_data_id:
                    try:
                        if amd_status == 'MACHINE':
                            cd_status = 'machine'
                        elif dtmf_pressed:
                            cd_status = 'completed'
                        elif duration > 0:
                            cd_status = 'answered'
                        else:
                            cd_status = 'failed'
                        await conn.execute(
                            "UPDATE campaign_data SET status=$1 WHERE id=$2",
                            cd_status, int(campaign_data_id)
                        )
                    except Exception:
                        pass

                # Update campaign counters
                if campaign_id:
                    try:
                        cid = int(campaign_id)
                        await conn.execute("""
                            UPDATE campaigns SET
                                completed = (SELECT COUNT(*) FROM campaign_data WHERE campaign_id=$1 AND status != 'pending'),
                                answered  = (SELECT COUNT(*) FROM campaign_data WHERE campaign_id=$1 AND status IN ('answered','completed')),
                                pressed_one = (SELECT COUNT(*) FROM calls WHERE campaign_id=$1 AND dtmf_pressed=1),
                                failed    = (SELECT COUNT(*) FROM campaign_data WHERE campaign_id=$1 AND status='failed'),
                                actual_cost = actual_cost + $2
                            WHERE id=$1
                        """, cid, cost)

                        # Deduct credits from campaign owner
                        if cost > 0:
                            user = await conn.fetchrow("SELECT user_id FROM campaigns WHERE id=$1", cid)
                            if user:
                                await conn.execute(
                                    "UPDATE users SET credits = GREATEST(0, credits - $2) WHERE id=$1",
                                    user['user_id'], cost
                                )
                    except Exception:
                        pass

            # P1 notification
            if dtmf_pressed and campaign_data_id:
                try:
                    async with self.db.pool.acquire() as conn2:
                        info = await conn2.fetchrow("""
                            SELECT cd.phone_number, c.name as campaign_name,
                                   u.telegram_id, u.p1_group_id
                            FROM campaign_data cd
                            JOIN campaigns c ON cd.campaign_id = c.id
                            JOIN users u ON c.user_id = u.id
                            WHERE cd.id = $1
                        """, int(campaign_data_id))
                        if info:
                            msg = (
                                f"🔔 <b>Press-1 Detected!</b>\n\n"
                                f"📞 Number: <code>{info['phone_number']}</code>\n"
                                f"📋 Campaign: {info['campaign_name']}\n"
                                f"⏱ Duration: {duration}s ✅"
                            )
                            await self._notify(info['telegram_id'], msg)
                            if info.get('p1_group_id'):
                                try:
                                    await self._notify(info['p1_group_id'], msg)
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"P1 notify error: {e}")

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"DTMF webhook error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    # =========================================================================
    # Asterisk Hangup webhook
    # =========================================================================

    async def handle_hangup(self, request):
        try:
            content_type = request.content_type or ''
            data = await request.json() if 'json' in content_type else dict(await request.post())
            logger.info(f"🔴 Hangup: {data}")

            call_id          = data.get('call_id', '')
            duration         = int(data.get('duration') or 0)
            hangup_cause     = data.get('hangup_cause', '')
            campaign_data_id = data.get('campaign_data_id', '')

            if not call_id:
                return web.json_response({"error": "No call_id"}, status=400)

            cost = self._calc_cost(duration)

            if hangup_cause in ('BUSY', 'USER_BUSY'):
                status = 'BUSY'
            elif hangup_cause in ('NO_ANSWER', 'NO_USER_RESPONSE'):
                status = 'NO ANSWER'
            elif hangup_cause in ('16', '17', 'NORMAL_CLEARING', 'NORMAL_CALL_CLEARING'):
                # hangup_cause 16 = normal call clearing = answered and completed normally
                status = 'ANSWER'
            elif duration > 0:
                status = 'ANSWER'
            else:
                status = 'FAILED'

            async with self.db.pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE calls
                    SET duration=COALESCE(NULLIF($1,0),duration), hangup_cause=$2, cost=$3,
                        ended_at=NOW(),
                        status=CASE WHEN status='COMPLETED' THEN status ELSE $4 END
                    WHERE call_id=$5
                """, duration, hangup_cause, cost, status, call_id)

                rows_updated = int(result.split()[-1])
                if rows_updated == 0 and campaign_data_id:
                    try:
                        await conn.execute("""
                            UPDATE calls
                            SET duration=COALESCE(NULLIF($1,0),duration), hangup_cause=$2,
                                cost=$3, ended_at=NOW(),
                                status=CASE WHEN status='COMPLETED' THEN status ELSE $4 END
                            WHERE campaign_data_id=$5
                        """, duration, hangup_cause, cost, status, int(campaign_data_id))
                    except Exception:
                        pass

                if campaign_data_id:
                    try:
                        # Only mark as failed if not already answered/completed
                        cd_status = 'failed' if status not in ('ANSWER', 'COMPLETED') else 'answered'
                        await conn.execute("""
                            UPDATE campaign_data SET status=$2
                            WHERE id=$1 AND status NOT IN ('completed', 'answered')
                        """, int(campaign_data_id), cd_status)
                    except Exception:
                        pass

            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.error(f"Hangup webhook error: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _calc_cost(self, duration: int) -> float:
        if duration <= 0:
            return 0.0
        billable = math.ceil(max(duration, 6) / 6) * 6
        return round(billable / 60 * 1.0, 4)

    async def _notify(self, chat_id: int, text: str):
        if not self.bot_app:
            return
        try:
            await self.bot_app.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Notify error ({chat_id}): {e}")