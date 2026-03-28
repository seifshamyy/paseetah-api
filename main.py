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
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
