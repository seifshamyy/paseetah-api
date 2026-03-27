"""
main.py
FastAPI application — exposes POST /api/v1/fetch-real-estate.
Handles 401/403 by refreshing the session and retrying once.
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from auth_service import AsyncAuthService, LoginError
from config import settings
from data_client import AsyncDataClient
from models import PaseetahDataRequest

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("paseetah.main")

# ---------------------------------------------------------------------------
# Global auth service (shared across requests)
# ---------------------------------------------------------------------------
auth_service = AsyncAuthService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load cached session on startup."""
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
        "Authenticated proxy to the Paseetah real estate platform. "
        "Handles reCAPTCHA bypass and session management automatically."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/v1/fetch-real-estate")
async def fetch_real_estate(request: PaseetahDataRequest):
    """
    Fetch sales transaction data from paseetah.com.

    - On success (HTTP 200 from Paseetah): returns the raw JSON payload.
    - On 401/403: refreshes the session via a fresh Playwright login and
      retries the request exactly once.
    - On repeated failure or CAPTCHA errors: returns HTTP 500.
    """
    # -----------------------------------------------------------------------
    # First attempt
    # -----------------------------------------------------------------------
    try:
        cookies = await auth_service.get_cookies()
        client = AsyncDataClient(cookies)
        data = await client.fetch(request)
        return JSONResponse(content=data)

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code

        if status not in (401, 403):
            logger.error(f"Paseetah returned unexpected status {status}: {exc}")
            raise HTTPException(
                status_code=502,
                detail=f"Paseetah API returned {status}: {exc.response.text[:500]}",
            )

        logger.warning(
            f"Received HTTP {status} from Paseetah. Refreshing session and retrying..."
        )

    # -----------------------------------------------------------------------
    # Session refresh + single retry
    # -----------------------------------------------------------------------
    try:
        await auth_service.invalidate_and_relogin()
    except LoginError as exc:
        logger.error(f"Re-login failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail=(
                "Authentication failed after receiving a session error from Paseetah. "
                f"Details: {exc}"
            ),
        )

    try:
        cookies = await auth_service.get_cookies()
        client = AsyncDataClient(cookies)
        data = await client.fetch(request)
        return JSONResponse(content=data)

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.error(
            f"Retry also failed with HTTP {status}. Giving up."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Paseetah returned HTTP {status} even after a fresh session refresh. "
                "Check credentials, CAPTCHA solver balance, or Paseetah service availability."
            ),
        )

    except Exception as exc:
        logger.exception(f"Unexpected error during retry: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during retry: {exc}",
        )


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
