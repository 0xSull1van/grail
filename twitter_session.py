"""Высокоуровневая обёртка над evm-овским twitter_client.

twitter_client.py — 1-в-1 копия из ardinals-orchestrator (login_with_cookies +
login_with_creds + IMAP email-код). Этот модуль:
  - выбирает между cred-mode (login+pass+email через IMAP) и cookie-mode
  - добавляет launch_chrome (в evm он был внутри login_*)
  - реализует follow_handle (evm не нужен был — у них флоу другой)
  - даёт debug-screenshots и нормальный логгинг
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import unquote

from patchright.async_api import BrowserContext, async_playwright

from twitter_client import (
    CookieAccount,
    CredAccount,
    TwitterLoginError,
    login_with_cookies,
    login_with_creds,
)

log = logging.getLogger("grail-bot.twitter")


def _proxy_kwargs(proxy_url: str | None) -> dict:
    if not proxy_url:
        return {}
    if "@" in proxy_url:
        scheme, rest = proxy_url.split("://", 1)
        creds, hostport = rest.split("@", 1)
        user, pw = creds.split(":", 1)
        return {
            "proxy": {
                "server": f"{scheme}://{hostport}",
                "username": unquote(user),
                "password": unquote(pw),
            }
        }
    return {"proxy": {"server": proxy_url}}


async def authenticate(
    account: CredAccount | CookieAccount,
    profile_dir: Path,
    headless: bool,
    timeout_sec: float = 90.0,
) -> BrowserContext:
    """Запускает Chrome через cred- или cookie-mode (по типу account).
    Возвращает BrowserContext с залогиненной сессией."""
    if isinstance(account, CredAccount):
        log.info(
            "auth via CREDS login=%s headless=%s proxy=%s",
            account.login, headless, account.proxy_url,
        )
        return await login_with_creds(account, profile_dir, headless=headless)
    if isinstance(account, CookieAccount):
        log.info(
            "auth via COOKIES headless=%s proxy=%s",
            headless, account.proxy_url,
        )
        return await login_with_cookies(account, profile_dir, headless=headless)
    raise TypeError(f"unknown account type: {type(account)}")


async def follow_handle(
    ctx: BrowserContext, handle: str, timeout_sec: float = 25.0
) -> bool:
    """Открывает x.com/<handle> и кликает Follow. True = подписаны (или уже были)."""
    handle = handle.lstrip("@").strip("/")
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    log.info("opening x.com/%s for follow", handle)
    try:
        await page.goto(
            f"https://x.com/{handle}",
            wait_until="domcontentloaded",
            timeout=int(timeout_sec * 1000),
        )
    except Exception as e:
        raise TwitterLoginError(f"profile load fail x.com/{handle}: {e}") from e

    await asyncio.sleep(2)

    unfollow = page.locator('[data-testid$="-unfollow"]')
    try:
        if await unfollow.count() > 0 and await unfollow.first.is_visible(timeout=1500):
            log.info("already following @%s", handle)
            return True
    except Exception:
        pass

    follow_candidates = [
        page.locator('[data-testid$="-follow"]'),
        page.get_by_role("button", name="Follow", exact=True),
        page.get_by_role("button", name="Читать", exact=True),
        page.get_by_role("button", name="Подписаться", exact=True),
    ]
    for loc in follow_candidates:
        try:
            target = loc.first
            await target.wait_for(state="visible", timeout=4000)
            try:
                await target.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            await target.click(timeout=5000)
            await asyncio.sleep(2)
            log.info("clicked Follow on @%s", handle)
            return True
        except Exception:
            continue
    log.warning("Follow button not found on @%s", handle)
    return False
