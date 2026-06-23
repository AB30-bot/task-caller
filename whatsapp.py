import aiohttp
from loguru import logger


class WhatsAppClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def send(self, to: str, message: str) -> bool:
        """Send a WhatsApp message via the OpenWA gateway.

        Returns True on success. Never raises — failures are logged and return False
        so a gateway outage never crashes the call pipeline.
        """
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(
                    f"{self.base_url}/send",
                    json={"to": to, "message": message},
                ) as r:
                    if r.status == 200:
                        return True
                    body = await r.text()
                    logger.warning(f"WhatsApp send failed: status={r.status} body={body[:200]}")
                    return False
        except Exception as e:
            logger.warning(f"WhatsApp send error (non-fatal): {e}")
            return False
