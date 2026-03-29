"""
main.py
FastAPI application — two endpoints:
  POST /api/v1/fetch-moj   → Ministry of Justice (sales_transaction)
  POST /api/v1/fetch-civil → Civil / Real-Estate Register (rer_transactions)
Both handle 401/403 by refreshing the session and retrying once.
"""

import logging
from contextlib import asynccontextmanager
from typing import Callable, Awaitable

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from auth_service import AsyncAuthService, LoginError
from config import settings
from data_client import AsyncDataClient
from geo_service import GeoService
from models import MojDataRequest, CivilDataRequest

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("paseetah.main")

# ---------------------------------------------------------------------------
# Global auth service
# ---------------------------------------------------------------------------
auth_service = AsyncAuthService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Paseetah API — loading session cache...")
    await auth_service.startup()
    yield
    logger.info("Paseetah API shutting down.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Paseetah Real Estate API",
    description=(
        "Authenticated proxy to Paseetah. Two datasets:\n\n"
        "- **MOJ** (`/api/v1/fetch-moj`): Ministry of Justice sales transactions\n"
        "- **Civil** (`/api/v1/fetch-civil`): Real Estate Register (RER) transactions"
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Shared retry helper
# ---------------------------------------------------------------------------
async def _fetch_with_retry(fetch_fn: Callable[..., Awaitable[dict]]) -> JSONResponse:
    """
    Calls fetch_fn(client) once. On 401/403, refreshes session and retries once.
    fetch_fn receives an AsyncDataClient built from fresh cookies.
    """
    # First attempt
    try:
        cookies = await auth_service.get_cookies()
        data = await fetch_fn(AsyncDataClient(cookies))
        return JSONResponse(content=data)

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status not in (401, 403):
            raise HTTPException(status_code=502, detail=f"Paseetah returned {status}")
        logger.warning(f"HTTP {status} — refreshing session and retrying...")

    # Re-login
    try:
        await auth_service.invalidate_and_relogin()
    except LoginError as exc:
        raise HTTPException(status_code=500, detail=f"Re-login failed: {exc}")

    # Retry
    try:
        cookies = await auth_service.get_cookies()
        data = await fetch_fn(AsyncDataClient(cookies))
        return JSONResponse(content=data)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Paseetah returned {exc.response.status_code} even after session refresh.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/refresh-session", summary="Keep session alive", tags=["Auth"])
async def refresh_session():
    alive = await auth_service.keepalive()
    if alive:
        return {"alive": True, "message": "Session is healthy."}
    return JSONResponse(
        status_code=401,
        content={"alive": False, "message": "Session expired — update SESSION_CACHE_JSON on Railway with fresh cookies."},
    )


@app.get("/api/v1/debug/xsrf", summary="Debug XSRF token state", tags=["Auth"])
async def debug_xsrf():
    """Shows raw XSRF-TOKEN cookie and decoded header value — use to diagnose 419/401 on POST endpoints."""
    from urllib.parse import unquote
    cookies = await auth_service.get_cookies()
    raw = cookies.get("XSRF-TOKEN", "MISSING")
    decoded = unquote(raw)
    return {
        "cookie_raw": raw[:80] + "..." if len(raw) > 80 else raw,
        "header_decoded": decoded[:80] + "..." if len(decoded) > 80 else decoded,
        "cookie_keys": list(cookies.keys()),
    }

@app.post(
    "/api/v1/fetch-moj",
    summary="Ministry of Justice — Sales Transactions",
    tags=["MOJ"],
)
async def fetch_moj(request: MojDataRequest):
    """
    Fetch transactions from the Ministry of Justice dataset.
    Filter by `regions`, `cities`, and/or `neighborhoods` (list of ints).
    """
    return await _fetch_with_retry(lambda client: client.fetch_moj(request))


@app.post(
    "/api/v1/fetch-civil",
    summary="Civil / Real-Estate Register — RER Transactions",
    tags=["Civil"],
)
async def fetch_civil(request: CivilDataRequest):
    """
    Fetch transactions from the Real Estate Register (civil records) dataset.
    Filter by `regions`, `cities`, and/or `neighborhoods` (list of ints).
    """
    return await _fetch_with_retry(lambda client: client.fetch_civil(request))


@app.get(
    "/api/v1/geo/probe",
    summary="Probe all candidate geo/neighborhood API endpoints",
    tags=["Geo"],
)
async def probe_geo():
    """
    Fires ~20 candidate Paseetah geo URLs in parallel using your session
    cookies and returns the HTTP status + response preview for each.
    200s are listed first — use this to discover which endpoints exist.
    """
    cookies = await auth_service.get_cookies()
    client = AsyncDataClient(cookies)
    results = await client.probe_geo_endpoints()
    return results


# ---------------------------------------------------------------------------
# Geo endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/geo/regions", summary="List all regions", tags=["Geo"])
async def geo_regions():
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_all_regions()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/cities", summary="Cities for a region", tags=["Geo"])
async def geo_cities(region_id: int):
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_cities_for_region(region_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/neighborhoods", summary="Neighborhoods for a city", tags=["Geo"])
async def geo_neighborhoods(city_id: int):
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_neighborhoods_for_city(city_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/tree", summary="Full city→neighborhood tree for a region", tags=["Geo"])
async def geo_tree(region_id: int):
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_full_tree(region_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/probe-neighborhoods", summary="Find correct neighborhood endpoint", tags=["Geo"])
async def probe_neighborhoods_endpoint(city_id: int = 1, region_id: int = 1):
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).probe_neighborhoods(city_id, region_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Named city neighborhood endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/geo/riyadh/neighborhoods", summary="All neighborhoods in Riyadh Region", tags=["Geo"])
async def riyadh_neighborhoods():
    """
    Returns all neighborhoods across every city in Riyadh Region (region_id=1).
    Each object: { id, name_en, name_ar, city_id, region_id }
    """
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_neighborhoods_by_region(region_id=1)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/mecca/neighborhoods", summary="All neighborhoods in Mecca Region", tags=["Geo"])
async def mecca_neighborhoods():
    """
    Returns all neighborhoods across every city in Mecca Region (region_id=3).
    Includes Jeddah, Mecca city, Taif, and all other cities in the region.
    Each object: { id, name_en, name_ar, city_id, region_id }
    """
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_neighborhoods_by_region(region_id=3)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/v1/geo/jeddah/neighborhoods", summary="All neighborhoods in Jeddah city only", tags=["Geo"])
async def jeddah_neighborhoods():
    """
    Returns all neighborhoods in Jeddah city specifically (city_id=16).
    Each object: { id, name_en, name_ar, city_id, region_id }
    """
    try:
        cookies = await auth_service.get_cookies()
        return await GeoService(cookies).get_neighborhoods_by_city(city_id=16)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
