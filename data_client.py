"""
data_client.py
Makes authenticated POST requests to the Paseetah API using
session cookies retrieved from AsyncAuthService.
Supports two endpoints:
  - MOJ  (Ministry of Justice): /api/precord/sales_transaction/data
  - Civil (RER):                 /api/precord/rer_transactions/data
"""

import logging
from urllib.parse import unquote

import httpx

from models import MojDataRequest, CivilDataRequest

logger = logging.getLogger(__name__)

MOJ_URL = "https://paseetah.com/api/precord/sales_transaction/data"
CIVIL_URL = "https://paseetah.com/api/precord/rer_transactions/data"

MOJ_REFERER = "https://paseetah.com/paseetah-record/sales_transaction"
CIVIL_REFERER = "https://paseetah.com/paseetah-record/rer_transactions"

COMMON_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Platform": "Chrome",
    "X-Request-Source": "web",
    "X-PDC-Filter-Level": "low",
    "X-PDC-Status": "off",
    "X-OS": "OS X",
    "Accept-Language": "ar",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


class AsyncDataClient:
    """
    Fetches real estate data from Paseetah.
    Requires a dictionary of session cookies obtained via AsyncAuthService.
    """

    def __init__(self, cookies: dict[str, str]) -> None:
        self._cookies = cookies

    def _build_headers(self, referer: str) -> dict[str, str]:
        headers = {**COMMON_HEADERS, "Referer": referer}
        xsrf_raw = self._cookies.get("XSRF-TOKEN", "")
        headers["X-XSRF-TOKEN"] = unquote(xsrf_raw)
        return headers

    async def _post(self, url: str, referer: str, payload: dict) -> dict:
        headers = self._build_headers(referer)
        logger.info(f"POST {url} | payload={payload}")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
                cookies=self._cookies,
            )
        logger.info(f"Response status: {response.status_code}")
        response.raise_for_status()
        return response.json()

    async def fetch_moj(self, request: MojDataRequest) -> dict:
        """Ministry of Justice — sales_transaction."""
        return await self._post(MOJ_URL, MOJ_REFERER, request.model_dump())

    async def fetch_civil(self, request: CivilDataRequest) -> dict:
        """Civil / Real-Estate Register — rer_transactions."""
        return await self._post(CIVIL_URL, CIVIL_REFERER, request.model_dump())

