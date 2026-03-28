"""
geo_service.py
Uses Playwright to load the Paseetah filter page with authenticated cookies,
then extracts region → city → neighborhood dropdown data from the Vue DOM.
Results are cached in geo_cache.json so we only scrape once.
"""

import asyncio
import json
import logging
import os
from typing import Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

GEO_CACHE_FILE = os.path.join(os.path.dirname(__file__), "geo_cache.json")
PASEETAH_FILTER_URL = "https://paseetah.com/paseetah-record/sales_transaction"


class GeoService:
    def __init__(self, cookies: dict[str, str]) -> None:
        self._cookies = cookies

    async def get_all_regions(self) -> list[dict]:
        cached = _load_geo_cache()
        if cached:
            return cached.get("regions", [])
        data = await self._scrape()
        return data.get("regions", [])

    async def get_cities_for_region(self, region_id: int) -> list[dict]:
        cached = _load_geo_cache()
        if not cached:
            cached = await self._scrape()
        for r in cached.get("regions", []):
            if r["id"] == region_id:
                return r.get("cities", [])
        return []

    async def get_neighborhoods_for_city(self, region_id: int, city_id: int) -> list[dict]:
        cached = _load_geo_cache()
        if not cached:
            cached = await self._scrape()
        for r in cached.get("regions", []):
            if r["id"] == region_id:
                for c in r.get("cities", []):
                    if c["id"] == city_id:
                        return c.get("neighborhoods", [])
        return []

    async def get_full_tree(self) -> dict:
        cached = _load_geo_cache()
        if cached:
            return cached
        return await self._scrape()

    async def _scrape(self) -> dict:
        logger.info("Starting Playwright geo scrape of Paseetah filter page...")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()

            # Inject session cookies
            playwright_cookies = [
                {"name": k, "value": v, "domain": "paseetah.com", "path": "/"}
                for k, v in self._cookies.items()
            ]
            await context.add_cookies(playwright_cookies)

            page = await context.new_page()

            # Intercept and log XHR to catch any dynamic geo requests
            geo_responses: list[dict] = []

            async def handle_response(response):
                url = response.url
                if "api" in url and response.status == 200:
                    try:
                        body = await response.json()
                        geo_responses.append({"url": url, "body": body})
                        logger.info(f"Captured API response: {url}")
                    except Exception:
                        pass

            page.on("response", handle_response)

            await page.goto(PASEETAH_FILTER_URL, wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(3)  # Let Vue finish rendering

            # Strategy 1: Extract from Vue component state via __vueParentComponent
            geo_data = await page.evaluate("""
                () => {
                    // Try to find the filter component that holds geo data
                    // Walk all DOM elements looking for Vue components with regions data
                    const allElements = document.querySelectorAll('*');
                    for (const el of allElements) {
                        const vueInstance = el.__vueParentComponent || el.__vue__;
                        if (!vueInstance) continue;
                        
                        // Check component data/props/setupState for regions
                        const sources = [
                            vueInstance.data,
                            vueInstance.setupState,
                            vueInstance.props,
                            vueInstance.ctx,
                        ];
                        for (const src of sources) {
                            if (!src) continue;
                            if (src.regions && Array.isArray(src.regions) && src.regions.length > 0) {
                                return { source: 'vueComponent', regions: src.regions };
                            }
                            if (src.cities && Array.isArray(src.cities) && src.cities.length > 0) {
                                return { source: 'vueComponent_cities', cities: src.cities };
                            }
                        }
                    }
                    return null;
                }
            """)

            # Strategy 2: Extract from the actual dropdown <select>/<option> or custom dropdown elements
            if not geo_data:
                logger.info("Vue component strategy failed, trying DOM select/option extraction...")
                geo_data = await page.evaluate("""
                    () => {
                        // Look for region select or list items
                        const results = { regions_dom: [] };
                        
                        // Check all select elements
                        const selects = document.querySelectorAll('select');
                        selects.forEach((sel, i) => {
                            const opts = Array.from(sel.options).map(o => ({
                                value: o.value, text: o.text.trim()
                            }));
                            if (opts.length > 1) results.regions_dom.push({ select_index: i, options: opts });
                        });
                        
                        // Check dropdowns powered by custom components (look for li items with data-value)
                        const listItems = document.querySelectorAll('[data-value], [data-id]');
                        const customOpts = Array.from(listItems).map(li => ({
                            value: li.getAttribute('data-value') || li.getAttribute('data-id'),
                            text: li.textContent.trim()
                        })).filter(x => x.value);
                        if (customOpts.length) results.custom_opts = customOpts;
                        
                        return results;
                    }
                """)

            # Strategy 3: Check Pinia/Vuex store
            if not geo_data or not geo_data.get("regions"):
                logger.info("Trying Pinia store extraction...")
                geo_data = await page.evaluate("""
                    () => {
                        // Pinia stores
                        if (window.__pinia) {
                            const stores = window.__pinia._s;
                            if (stores) {
                                const result = {};
                                stores.forEach((store, id) => {
                                    const s = store.$state || store;
                                    if (s.regions || s.cities || s.neighborhoods) {
                                        result[id] = { regions: s.regions, cities: s.cities, neighborhoods: s.neighborhoods };
                                    }
                                });
                                if (Object.keys(result).length) return { source: 'pinia', data: result };
                            }
                        }
                        // Vuex store
                        if (window.__vue_store__ || window.$store) {
                            const store = window.__vue_store__ || window.$store;
                            return { source: 'vuex', state: store.state };
                        }
                        return null;
                    }
                """)

            # Strategy 4: Check window-level variables
            if not geo_data:
                logger.info("Trying window variable extraction...")
                geo_data = await page.evaluate("""
                    () => {
                        const keys = Object.keys(window).filter(k => 
                            !['undefined','null','NaN','Infinity','location','document','window'].includes(k)
                        );
                        for (const k of keys) {
                            try {
                                const v = window[k];
                                if (v && typeof v === 'object') {
                                    if (v.regions && Array.isArray(v.regions)) return { source: 'window.' + k, data: v };
                                }
                            } catch(e) {}
                        }
                        return null;
                    }
                """)

            await browser.close()

        # Build result — combine intercepted API responses + scraped DOM
        result = {
            "geo_data": geo_data,
            "intercepted_api_calls": geo_responses,
        }

        # Try to build a structured tree from the result
        structured = _try_build_tree(result)
        if structured:
            _save_geo_cache(structured)
            return structured

        # Return raw result if we couldn't build a clean tree
        return result


def _try_build_tree(raw: dict) -> Optional[dict]:
    """Attempt to extract a clean { regions: [{id, name, cities: [...]}] } from raw."""
    # Check if intercepted API calls contain geo data
    for call in raw.get("intercepted_api_calls", []):
        body = call.get("body", {})
        if isinstance(body, dict):
            if "regions" in body or "data" in body:
                regions = body.get("regions") or body.get("data", [])
                if isinstance(regions, list) and regions:
                    return {"regions": regions, "source": call["url"]}
        if isinstance(body, list) and body:
            if "id" in body[0] or "region_id" in body[0]:
                return {"regions": body, "source": call["url"]}

    # Check geo_data from Vue
    geo = raw.get("geo_data")
    if geo and isinstance(geo, dict):
        if "regions" in geo and isinstance(geo["regions"], list) and geo["regions"]:
            return {"regions": geo["regions"], "source": "vue_component"}
    return None


def _load_geo_cache() -> Optional[dict]:
    if not os.path.exists(GEO_CACHE_FILE):
        return None
    try:
        with open(GEO_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data:
            logger.info("Loaded geo data from cache.")
            return data
    except Exception as exc:
        logger.warning(f"Geo cache read failed: {exc}")
    return None


def _save_geo_cache(data: dict) -> None:
    try:
        with open(GEO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Geo data cached to {GEO_CACHE_FILE}")
    except Exception as exc:
        logger.error(f"Failed to save geo cache: {exc}")
