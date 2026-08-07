# =============================================================================
# LoyalCorp P1 Bot — Oxapay Payment Handler
# =============================================================================

import aiohttp
import logging
import uuid
from config import OXAPAY_API_KEY, OXAPAY_API_URL, DEFAULT_CURRENCY, WEBHOOK_URL

logger = logging.getLogger(__name__)


class OxapayHandler:
    def __init__(self):
        self.api_key  = OXAPAY_API_KEY
        self.api_url  = OXAPAY_API_URL

    async def create_invoice(self, amount: float, description: str = "",
                              order_id: str = None) -> dict:
        if not order_id:
            order_id = str(uuid.uuid4())[:16]

        payload = {
            "amount":              amount,
            "currency":            DEFAULT_CURRENCY,
            "lifetime":            30,
            "fee_paid_by_payer":   0,
            "under_paid_coverage": 2.5,
            "description":         description or f"LoyalCorp P1 — ${amount:.2f} credits",
            "order_id":            order_id,
            "callback_url":        f"{WEBHOOK_URL}/webhook/oxapay",
            "return_url":          "",
        }
        headers = {
            "merchant_api_key": self.api_key,
            "Content-Type":     "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    logger.info(f"Oxapay invoice response: {data}")
                    if data.get("status") == 200:
                        inner = data.get("data", {})
                        return {
                            "success":     True,
                            "track_id":    inner.get("track_id", ""),
                            "payment_url": inner.get("payment_url", ""),
                            "order_id":    order_id,
                        }
                    return {"success": False, "error": str(data)}
        except Exception as e:
            logger.error(f"Oxapay error: {e}")
            return {"success": False, "error": str(e)}

    async def check_payment_status(self, track_id: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.oxapay.com/v1/payment/inquiry",
                    json={"track_id": track_id},
                    headers={"merchant_api_key": self.api_key, "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    # Normalize status field for existing callers
                    inner = data.get("data", {})
                    if inner.get("status"):
                        data["status_text"] = inner["status"]
                    return data
        except Exception as e:
            logger.error(f"Oxapay status check error: {e}")
            return {}


oxapay = OxapayHandler()