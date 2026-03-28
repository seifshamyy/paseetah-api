"""
data_client.py
Makes authenticated POST requests to the Paseetah API using
session cookies retrieved from AsyncAuthService.
Supports two endpoints:
  - MOJ  (Ministry of Justice): /api/precord/sales_transaction/data
  - Civil (RER):                 /api/precord/rer_transactions/data
"""

import asyncio
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

# Candidate geo URLs to probe (region_id=1 baked in where relevant)
GEO_CANDIDATES = [
    ("GET",  "https://paseetah.com/api/regions",                          None),
    ("GET",  "https://paseetah.com/api/cities?region_id=1",               None),
    ("GET",  "https://paseetah.com/api/neighborhoods?region_id=1",        None),
    ("GET",  "https://paseetah.com/api/neighborhoods?city_id=1",          None),
    ("GET",  "https://paseetah.com/api/geo",                              None),
    ("GET",  "https://paseetah.com/api/geo/regions",                      None),
    ("GET",  "https://paseetah.com/api/geo/cities?region_id=1",           None),
    ("GET",  "https://paseetah.com/api/geo/neighborhoods?region_id=1",    None),
    ("GET",  "https://paseetah.com/api/lookup/regions",                   None),
    ("GET",  "https://paseetah.com/api/lookup/cities?region_id=1",        None),
    ("GET",  "https://paseetah.com/api/lookup/neighborhoods?region_id=1", None),
    ("GET",  "https://paseetah.com/api/precord/regions",                  None),
    ("GET",  "https://paseetah.com/api/precord/cities?region_id=1",       None),
    ("GET",  "https://paseetah.com/api/precord/neighborhoods?region_id=1",None),
    ("POST", "https://paseetah.com/api/regions",                          {"region_id": 1}),
    ("POST", "https://paseetah.com/api/cities",                           {"region_id": 1}),
    ("POST", "https://paseetah.com/api/neighborhoods",                    {"region_id": 1, "city_id": 1}),
    ("POST", "https://paseetah.com/api/precord/regions",                  {}),
    ("POST", "https://paseetah.com/api/precord/cities",                   {"region_id": 1}),
    ("POST", "https://paseetah.com/api/precord/neighborhoods",            {"region_id": 1}),
]


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

    async def probe_geo_endpoints(self) -> list[dict]:
        """
        Fire all GEO_CANDIDATES in parallel, return status + response
        preview for each so we can discover which endpoints exist.
        """
        headers = self._build_headers("https://paseetah.com/")
        results = []

        async def _probe(method: str, url: str, body):
            entry = {"method": method, "url": url, "status": None, "preview": None, "error": None}
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    if method == "GET":
                        r = await client.get(url, headers=headers, cookies=self._cookies)
                    else:
                        r = await client.post(url, json=body or {}, headers=headers, cookies=self._cookies)
                entry["status"] = r.status_code
                # Return first 600 chars of response
                entry["preview"] = r.text[:600]
            except Exception as exc:
                entry["error"] = str(exc)
            return entry

        tasks = [_probe(m, u, b) for m, u, b in GEO_CANDIDATES]
        results = await asyncio.gather(*tasks)
        # Sort: 200s first, then by status code
        return sorted(results, key=lambda x: (x["status"] != 200, x["status"] or 999))
