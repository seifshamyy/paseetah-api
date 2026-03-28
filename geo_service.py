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
        """
        Use Playwright to click through the dropdowns on the Paseetah filter page
        and intercept EVERY XHR/fetch that fires after each interaction.
        This captures the real neighborhood API call that only triggers on user action.
        """
        import asyncio as _asyncio
        from playwright.async_api import async_playwright

        intercepted: list[dict] = []
        FILTER_URL = "https://paseetah.com/paseetah-record/sales_transaction"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()

            # Inject cookies
            playwright_cookies = [
                {"name": k, "value": v, "domain": "paseetah.com", "path": "/"}
                for k, v in self._cookies.items()
            ]
            await context.add_cookies(playwright_cookies)
            page = await context.new_page()

            # Intercept ALL responses
            async def on_response(response):
                url = response.url
                if "paseetah.com/api" in url:
                    try:
                        body = await response.json()
                    except Exception:
                        body = await response.text()
                    intercepted.append({
                        "url": url,
                        "status": response.status,
                        "body_preview": str(body)[:500],
                    })
                    logger.info(f"Intercepted: {response.status} {url}")

            page.on("response", on_response)

            logger.info(f"Loading {FILTER_URL} with session cookies...")
            await page.goto(FILTER_URL, wait_until="networkidle", timeout=60_000)
            await _asyncio.sleep(2)

            # Try to click on the region dropdown and select the first option
            # Paseetah uses a custom Vue select component — find it and interact
            logger.info("Attempting to interact with region dropdown...")

            # Look for clickable filter/select elements and click all of them
            # then wait for any network activity
            clicked_something = False
            for selector in [
                "select",
                "[role='combobox']",
                "[role='listbox']",
                ".multiselect",
                ".v-select",
                ".select",
                "input[type='text'][placeholder*='منطقة'], input[type='text'][placeholder*='region']",
                "div[class*='select'], div[class*='filter'], div[class*='dropdown']",
            ]:
                try:
                    els = await page.locator(selector).all()
                    if els:
                        logger.info(f"Found {len(els)} elements matching '{selector}'")
                        for el in els[:3]:  # try first 3
                            try:
                                await el.click(timeout=3000)
                                await _asyncio.sleep(1)
                                clicked_something = True
                                # Try clicking the first option
                                for opt_sel in ["li", "option", "[role='option']", ".multiselect__element"]:
                                    opts = await page.locator(opt_sel).all()
                                    if opts:
                                        await opts[0].click(timeout=2000)
                                        await _asyncio.sleep(2)
                                        break
                            except Exception:
                                pass
                except Exception:
                    pass

            # Also capture all current session values by evaluating Vue state
            vue_state = await page.evaluate("""
                () => {
                    // Try Pinia
                    if (window.__pinia) {
                        const result = {};
                        window.__pinia._s.forEach((store, id) => {
                            const s = store.$state || store;
                            const keys = ['regions','cities','neighborhoods','districts','areas'];
                            for (const k of keys) {
                                if (s[k] && Array.isArray(s[k]) && s[k].length > 0) {
                                    result[id + '.' + k] = s[k].slice(0, 5);
                                }
                            }
                        });
                        if (Object.keys(result).length) return { source: 'pinia', data: result };
                    }
                    return null;
                }
            """)

            await browser.close()

        return {
            "intercepted_api_calls": intercepted,
            "vue_pinia_state": vue_state,
            "clicked_dropdowns": clicked_something,
        }



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
