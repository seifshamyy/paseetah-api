"""
geo_service.py
Direct httpx calls to Paseetah's discovered geo API:
  GET /api/paseetah-record/get-regions
  GET /api/paseetah-record/get-cities?region_id={id}
  GET /api/paseetah-record/get-neighborhoods?city_id={id}
Results are cached in geo_cache.json.
"""

import json
import logging
import os
from urllib.parse import unquote
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GEO_CACHE_FILE = os.path.join(os.path.dirname(__file__), "geo_cache.json")
BASE = "https://paseetah.com/api/paseetah-record"

GEO_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Platform": "Chrome",
    "X-Request-Source": "web",
    "X-PDC-Filter-Level": "low",
    "X-PDC-Status": "off",
    "X-OS": "OS X",
    "Accept-Language": "ar",
    "Referer": "https://paseetah.com/paseetah-record/sales_transaction",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


class GeoService:
    def __init__(self, cookies: dict[str, str]) -> None:
        self._cookies = cookies
        self._headers = {
            **GEO_HEADERS,
            "X-XSRF-TOKEN": unquote(cookies.get("XSRF-TOKEN", "")),
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_all_regions(self) -> list[dict]:
        cache = _load_cache()
        if cache and "regions" in cache:
            return cache["regions"]
        return await self._fetch_regions()

    async def get_cities_for_region(self, region_id: int) -> list[dict]:
        return await self._fetch_cities(region_id)

    async def get_neighborhoods_for_city(self, city_id: int) -> list[dict]:
        return await self._fetch_neighborhoods(city_id)

    async def get_full_tree(self, region_id: int) -> list[dict]:
        """
        Return all cities + neighborhoods for a given region.
        Builds:  [ { city_id, city_name_en, city_name_ar, neighborhoods: [...] } ]
        """
        cities = await self._fetch_cities(region_id)
        result = []
        for city in cities:
            cid = city.get("id")
            try:
                hoods = await self._fetch_neighborhoods(cid) if cid else []
            except Exception:
                hoods = []
            result.append({**city, "neighborhoods": hoods})
        return result

    async def probe_neighborhoods(self, city_id: int = 1, region_id: int = 1) -> list[dict]:
        """Try all plausible neighborhood URL + param combinations in parallel."""
        import asyncio as _asyncio

        base = "https://paseetah.com/api"
        route_names = [
            "get-neighborhoods", "get-neighborhood",
            "get-districts",     "get-district",
            "get-areas",         "get-area",
            "get-sub-cities",    "get-quarters",
            "get-zones",         "get-blocks",
        ]
        prefixes = ["paseetah-record", "precord"]
        param_sets = [
            {"city_id": city_id},
            {"region_id": region_id, "city_id": city_id},
            {"city": city_id},
            {"id": city_id},
        ]

        candidates = []
        for prefix in prefixes:
            for route in route_names:
                for params in param_sets:
                    candidates.append((f"{base}/{prefix}/{route}", params))

        results = []

        async def _try(url, params):
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                    r = await client.get(url, params=params, headers=self._headers, cookies=self._cookies)
                return {"url": url, "params": params, "status": r.status_code, "preview": r.text[:300]}
            except Exception as e:
                return {"url": url, "params": params, "status": None, "error": str(e)}

        tasks = [_try(u, p) for u, p in candidates]
        results = await _asyncio.gather(*tasks)
        return sorted(results, key=lambda x: (x.get("status") != 200, x.get("status") or 999))


    # ------------------------------------------------------------------
    # Internal fetchers
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: dict = None) -> list:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                url,
                params=params,
                headers=self._headers,
                cookies=self._cookies,
            )
        resp.raise_for_status()
        data = resp.json()
        # Paseetah wraps in { data: [...] } or returns a list directly
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data") or data.get("regions") or data.get("cities") or data.get("neighborhoods") or []
        return []

    async def _fetch_regions(self) -> list[dict]:
        logger.info("Fetching regions from Paseetah geo API...")
        regions = await self._get(f"{BASE}/get-regions")
        _save_cache({"regions": regions})
        return regions

    async def _fetch_cities(self, region_id: int) -> list[dict]:
        logger.info(f"Fetching cities for region_id={region_id}...")
        return await self._get(f"{BASE}/get-cities", params={"region_id": region_id})

    async def _fetch_neighborhoods(self, city_id: int) -> list[dict]:
        logger.info(f"Fetching neighborhoods for city_id={city_id}...")
        return await self._get(f"{BASE}/get-neighborhoods", params={"city_id": city_id})


# ------------------------------------------------------------------
# Cache helpers
# ------------------------------------------------------------------

def _load_cache() -> Optional[dict]:
    if not os.path.exists(GEO_CACHE_FILE):
        return None
    try:
        with open(GEO_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        with open(GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error(f"Failed to save geo cache: {exc}")
