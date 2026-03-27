"""
auth_service.py
Handles Playwright-based login to paseetah.com,
reCAPTCHA v2 bypass via CapSolver, and session cookie caching.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext

from config import settings

logger = logging.getLogger(__name__)

CAPSOLVER_CREATE_URL = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT_URL = "https://api.capsolver.com/getTaskResult"
CAPTCHA_POLL_INTERVAL = 5  # seconds
CAPTCHA_POLL_MAX_ATTEMPTS = 30
MAX_CAPTCHA_RETRIES = 3


class CaptchaSolverError(Exception):
    pass


class LoginError(Exception):
    pass


async def _solve_recaptcha(site_key: str, page_url: str) -> str:
    """
    Sends a RecaptchaV2TaskProxyless task to CapSolver and polls until
    a valid token is returned.
    Raises CaptchaSolverError after MAX_CAPTCHA_RETRIES failures.
    """
    if not settings.CAPTCHA_SOLVER_API_KEY:
        raise CaptchaSolverError(
            "CAPTCHA_SOLVER_API_KEY is not set in environment variables."
        )

    payload = {
        "clientKey": settings.CAPTCHA_SOLVER_API_KEY,
        "task": {
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": site_key,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        logger.info("Submitting reCAPTCHA task to CapSolver...")
        resp = await client.post(CAPSOLVER_CREATE_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if data.get("errorId", 0) != 0:
        raise CaptchaSolverError(
            f"CapSolver createTask error: {data.get('errorDescription', data)}"
        )

    task_id: str = data["taskId"]
    logger.info(f"CapSolver task created: {task_id}. Polling for result...")

    result_payload = {
        "clientKey": settings.CAPTCHA_SOLVER_API_KEY,
        "taskId": task_id,
    }

    for attempt in range(CAPTCHA_POLL_MAX_ATTEMPTS):
        await asyncio.sleep(CAPTCHA_POLL_INTERVAL)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(CAPSOLVER_RESULT_URL, json=result_payload)
            resp.raise_for_status()
            result = resp.json()

        status = result.get("status")
        logger.debug(f"CapSolver poll attempt {attempt + 1}: status={status}")

        if status == "ready":
            token = result["solution"]["gRecaptchaResponse"]
            logger.info("CapSolver returned a valid reCAPTCHA token.")
            return token

        if status != "processing":
            raise CaptchaSolverError(
                f"CapSolver unexpected status: {status} — {result}"
            )

    raise CaptchaSolverError(
        "CapSolver timed out: maximimum polling attempts reached without a result."
    )


class AsyncAuthService:
    """
    Manages authentication state for paseetah.com.
    Caches session cookies in session_cache.json to avoid re-logging in
    on every request.
    """

    def __init__(self) -> None:
        self._cookies: Optional[dict[str, str]] = None
        self._cache_file: str = settings.SESSION_CACHE_FILE

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Load cached cookies from disk if available."""
        self._cookies = self._load_cache()
        if self._cookies:
            logger.info("Loaded session cookies from cache.")
        else:
            logger.info("No cached session found. Login will be triggered on first request.")

    async def get_cookies(self) -> dict[str, str]:
        """Return cached cookies, performing login first if necessary."""
        if not self._cookies:
            await self.perform_login()
        return self._cookies  # type: ignore[return-value]

    async def invalidate_and_relogin(self) -> None:
        """Force a fresh login (called on 401/403 from data client)."""
        logger.warning("Session invalidated. Performing fresh login...")
        self._cookies = None
        _remove_cache(self._cache_file)
        await self.perform_login()

    # ------------------------------------------------------------------
    # Playwright login flow
    # ------------------------------------------------------------------

    async def perform_login(self) -> None:
        """
        Open a headless Chromium browser, navigate to paseetah.com,
        fill credentials, solve reCAPTCHA, submit the form, and extract
        the resulting session cookies.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_CAPTCHA_RETRIES + 1):
            logger.info(f"Login attempt {attempt}/{MAX_CAPTCHA_RETRIES}...")
            try:
                cookies = await self._run_playwright_login()
                self._cookies = cookies
                self._save_cache(cookies)
                logger.info("Login successful. Cookies cached.")
                return
            except CaptchaSolverError as exc:
                logger.error(f"CAPTCHA solve failed on attempt {attempt}: {exc}")
                last_error = exc
            except Exception as exc:
                logger.exception(f"Unexpected error during login attempt {attempt}: {exc}")
                last_error = exc
                # Non-CAPTCHA errors are not retried more than once
                break

        raise LoginError(
            f"Login failed after {MAX_CAPTCHA_RETRIES} attempt(s). "
            f"Last error: {last_error}"
        )

    async def _run_playwright_login(self) -> dict[str, str]:
        """Single Playwright login attempt. Returns cookie dict on success."""
        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(headless=True)
            context: BrowserContext = await browser.new_context()
            page = await context.new_page()

            try:
                logger.info("Navigating to https://paseetah.com/")
                await page.goto("https://paseetah.com/", wait_until="networkidle", timeout=60_000)

                # Click the login trigger button
                logger.info("Clicking login trigger...")
                login_trigger = page.locator("div.try-btn:has-text('تسجيل الدخول')")
                await login_trigger.first.click()
                await page.wait_for_load_state("networkidle", timeout=15_000)

                # Fill email
                email_input = page.locator("input[type='email']")
                await email_input.first.fill(settings.PASEETAH_EMAIL)

                # Fill password
                password_input = page.locator("input[type='password']")
                await password_input.first.fill(settings.PASEETAH_PASSWORD)

                # Extract reCAPTCHA sitekey
                logger.info("Extracting reCAPTCHA sitekey...")
                site_key: Optional[str] = await page.evaluate(
                    """() => {
                        const el = document.querySelector('[data-sitekey]');
                        return el ? el.getAttribute('data-sitekey') : null;
                    }"""
                )
                if not site_key:
                    raise LoginError("Could not find reCAPTCHA sitekey on the page.")

                logger.info(f"reCAPTCHA sitekey: {site_key}")

                # Solve CAPTCHA
                current_url = page.url
                token = await _solve_recaptcha(site_key, current_url)

                # Inject token into the hidden textarea
                await page.evaluate(
                    f"document.getElementById('g-recaptcha-response').innerHTML = '{token}';"
                )

                # Fire the reCAPTCHA callback to notify the framework
                await page.evaluate(
                    """(token) => {
                        // Try ___grecaptcha_cfg callback first (standard)
                        if (window.___grecaptcha_cfg) {
                            const clients = window.___grecaptcha_cfg.clients;
                            if (clients) {
                                for (const key of Object.keys(clients)) {
                                    const client = clients[key];
                                    for (const field of Object.values(client)) {
                                        if (field && typeof field.callback === 'function') {
                                            field.callback(token);
                                            return;
                                        }
                                    }
                                }
                            }
                        }
                        // Fallback: try explicit render callback stored on window
                        if (typeof window.verifyCallback === 'function') {
                            window.verifyCallback(token);
                        }
                    }""",
                    token,
                )

                # Click the submit button
                logger.info("Submitting login form...")
                submit_btn = page.locator("button[type='submit']")
                await submit_btn.first.click()

                # Wait for the session cookie to appear (up to 30 s)
                logger.info("Waiting for paseetah_session cookie...")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    raw_cookies = await context.cookies()
                    names = {c["name"] for c in raw_cookies}
                    if "paseetah_session" in names:
                        break
                    await asyncio.sleep(1)
                else:
                    raise LoginError(
                        "Timed out waiting for paseetah_session cookie after form submission."
                    )

                # Extract all cookies
                raw_cookies = await context.cookies()
                cookie_dict: dict[str, str] = {c["name"]: c["value"] for c in raw_cookies}
                logger.info(f"Captured cookies: {list(cookie_dict.keys())}")
                return cookie_dict

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self) -> Optional[dict[str, str]]:
        # 1. Prefer the env var (Railway / Docker deployments)
        if settings.SESSION_CACHE_JSON:
            try:
                data = json.loads(settings.SESSION_CACHE_JSON)
                if isinstance(data, dict) and data:
                    logger.info("Loaded session cookies from SESSION_CACHE_JSON env var.")
                    return data
            except json.JSONDecodeError as exc:
                logger.warning(f"SESSION_CACHE_JSON is set but invalid JSON: {exc}")

        # 2. Fall back to file on disk
        if not os.path.exists(self._cache_file):
            return None
        try:
            with open(self._cache_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data:
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Could not read session cache: {exc}")
        return None

    def _save_cache(self, cookies: dict[str, str]) -> None:
        try:
            with open(self._cache_file, "w", encoding="utf-8") as fh:
                json.dump(cookies, fh, indent=2)
            logger.info(f"Session cookies saved to {self._cache_file}")
        except OSError as exc:
            logger.error(f"Failed to save session cache: {exc}")


def _remove_cache(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
