"""
data_client.py
Makes authenticated POST requests to the Paseetah API using
session cookies retrieved from AsyncAuthService.
"""

import logging
from urllib.parse import unquote

import httpx

from models import PaseetahDataRequest

logger = logging.getLogger(__name__)

PASEETAH_DATA_URL = "https://paseetah.com/api/precord/sales_transaction/data"

BASE_HEADERS = {
    "Referer": "https://paseetah.com/paseetah-record/sales_transaction",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Platform": "Chrome",
    "X-Request-Source": "web",
    "X-PDC-Filter-Level": "low",
    "X-PDC-Status": "off",
    "X-OS": "OS X",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


class AsyncDataClient:
    """
    Fetches real estate sales transaction data from Paseetah.
    Requires a dictionary of session cookies obtained via AsyncAuthService.
    """

    def __init__(self, cookies: dict[str, str]) -> None:
        self._cookies = cookies

    def _build_headers(self) -> dict[str, str]:
        """
        Construct request headers. Decodes the XSRF-TOKEN cookie value
        via urllib.parse.unquote and sets it as X-XSRF-TOKEN.
        """
        headers = dict(BASE_HEADERS)
        xsrf_raw = self._cookies.get("XSRF-TOKEN", "")
        headers["X-XSRF-TOKEN"] = unquote(xsrf_raw)
        logger.debug(f"X-XSRF-TOKEN set (length={len(headers['X-XSRF-TOKEN'])})")
        return headers

    async def fetch(self, request: PaseetahDataRequest) -> dict:
        """
        POST the request payload to the Paseetah data endpoint.

        Returns:
            Parsed JSON response body.

        Raises:
            httpx.HTTPStatusError: on non-2xx responses (caller handles 401/403).
        """
        headers = self._build_headers()
        payload = request.model_dump()

        logger.info(
            f"POSTing to {PASEETAH_DATA_URL} "
            f"| page={request.page} regions={request.regions} cities={request.cities}"
        )

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                PASEETAH_DATA_URL,
                json=payload,
                headers=headers,
                cookies=self._cookies,
            )

        logger.info(f"Paseetah API response status: {response.status_code}")
        response.raise_for_status()
        return response.json()
