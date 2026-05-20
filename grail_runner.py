"""Прогон одного аккаунта через grail-флоу.

Шаги:
  1. Открыть https://grails.fancraze.com/connect
  2. Клик "Connect X" → x.com/oauth2/authorize
  3. Клик "Authorize app"
  4. Дождаться редиректа на grails.fancraze.com
  5. Шаг email — ввести email + Continue
  6. Best-effort: пройти оставшиеся шаги (Continue/Skip)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import logging
from patchright.async_api import BrowserContext, Page

log = logging.getLogger("grail-bot.grail")


class GrailFlowError(Exception):
    pass


@dataclass
class GrailResult:
    x_connected: bool
    email_submitted: bool
    final_url: str
    handle: str | None


async def _click_first_visible(page: Page, locators, timeout_each_ms: int = 6000) -> bool:
    for loc in locators:
        try:
            target = loc.first
            try:
                await target.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            await target.click(timeout=timeout_each_ms)
            return True
        except Exception:
            continue
    return False


async def _dismiss_x_cookie_banner(page: Page) -> None:
    try:
        for label in (
            "Accept all cookies",
            "Принять все файлы cookie",
            "Refuse non-essential cookies",
            "Отказаться от несущественных файлов cookie",
        ):
            btn = page.get_by_role("button", name=label, exact=False)
            if await btn.count() > 0:
                try:
                    await btn.first.click(timeout=3000)
                    await asyncio.sleep(1)
                    return
                except Exception:
                    continue
    except Exception:
        pass


async def _click_authorize_app(page: Page, timeout_sec: float = 25.0) -> None:
    await _dismiss_x_cookie_banner(page)

    deadline = asyncio.get_event_loop().time() + timeout_sec
    candidates = [
        page.get_by_role("button", name="Authorize app", exact=False),
        page.get_by_role("button", name="Authorize", exact=True),
        page.get_by_role("button", name="Авторизовать приложение", exact=False),
        page.get_by_role("button", name="Авторизовать", exact=True),
        page.get_by_role("button", name="Allow", exact=False),
        page.get_by_role("button", name="Разрешить", exact=False),
        page.locator('button[data-testid="OAuth_Consent_Button"]'),
        page.locator('button:has-text("Authorize")'),
        page.locator('button:has-text("Авторизовать")'),
    ]
    while asyncio.get_event_loop().time() < deadline:
        for loc in candidates:
            try:
                target = loc.first
                if await target.is_visible(timeout=800):
                    try:
                        await target.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    await target.click(timeout=6000)
                    return
            except Exception:
                continue
        await asyncio.sleep(1)
    raise GrailFlowError(f"Authorize button not found on {page.url}")


async def _fill_email_step(page: Page, email: str | None) -> bool:
    if not email:
        return False

    email_input_candidates = [
        page.locator('input[type="email"]'),
        page.locator('input[name="email"]'),
        page.locator('input[placeholder*="mail" i]'),
        page.locator('input[autocomplete="email"]'),
    ]
    input_loc = None
    for loc in email_input_candidates:
        try:
            if await loc.count() > 0 and await loc.first.is_visible(timeout=1500):
                input_loc = loc.first
                break
        except Exception:
            continue
    if input_loc is None:
        return False

    try:
        await input_loc.fill("")
    except Exception:
        pass
    await input_loc.type(email, delay=40)
    await asyncio.sleep(0.5)

    continue_candidates = [
        page.get_by_role("button", name="Continue", exact=False),
        page.get_by_role("button", name="Submit", exact=False),
        page.get_by_role("button", name="Next", exact=False),
        page.locator('button[type="submit"]'),
    ]
    if not await _click_first_visible(page, continue_candidates, 5000):
        return False
    await asyncio.sleep(2)
    return True


async def _try_advance_step(page: Page) -> bool:
    candidates = [
        page.get_by_role("button", name="Continue", exact=False),
        page.get_by_role("button", name="Next", exact=False),
        page.get_by_role("button", name="Skip", exact=False),
        page.get_by_role("button", name="Done", exact=False),
        page.get_by_role("button", name="Finish", exact=False),
    ]
    for loc in candidates:
        try:
            target = loc.first
            if not await target.is_visible(timeout=1500):
                continue
            disabled = await target.evaluate(
                "n => n.disabled || n.getAttribute('aria-disabled') === 'true'"
            )
            if disabled:
                continue
            await target.click(timeout=4000)
            await asyncio.sleep(2)
            return True
        except Exception:
            continue
    return False


async def run_grail_flow(
    ctx: BrowserContext,
    grail_url: str,
    email: str | None,
    oauth_callback_timeout_sec: float = 45.0,
    post_oauth_settle_sec: float = 4.0,
) -> GrailResult:
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    log.info("opening grail /connect: %s", grail_url)
    await page.goto(grail_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)
    log.info("grail page loaded, url=%s title=%s", page.url, await page.title())

    # Вместо клика по ссылке — навигируем прямо на start URL.
    # React может не отдавать clickable-link после гидрации, поэтому прямой goto надёжнее.
    start_url = grail_url.rstrip("/").rsplit("/", 1)[0] + "/api/auth/x/start"
    log.info("navigating directly to OAuth start: %s", start_url)
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass  # редирект на x.com прервёт domcontentloaded — это нормально

    log.info("after start goto, url=%s", page.url)

    # Ждём пока окажемся на x.com OAuth
    try:
        await page.wait_for_url(
            lambda u: "x.com/i/oauth2" in u or "twitter.com/i/oauth2" in u
                      or "/oauth2/authorize" in u,
            timeout=int(oauth_callback_timeout_sec * 1000),
        )
    except Exception as e:
        log.error("OAuth redirect timeout, current url=%s", page.url)
        raise GrailFlowError(
            f"no redirect to x.com OAuth (URL: {page.url}): {e}"
        ) from e

    log.info("on x.com OAuth page: %s", page.url)
    await asyncio.sleep(2)
    log.info("clicking Authorize app button...")
    await _click_authorize_app(page, timeout_sec=20)
    log.info("Authorize clicked, waiting for grail callback...")

    try:
        await page.wait_for_url(
            lambda u: "grails.fancraze.com" in u,
            timeout=int(oauth_callback_timeout_sec * 1000),
        )
    except Exception as e:
        log.error("grail callback timeout, current url=%s", page.url)
        raise GrailFlowError(
            f"OAuth callback never returned to grail (URL: {page.url}): {e}"
        ) from e

    log.info("back on grail: %s", page.url)

    x_connected = True
    await asyncio.sleep(post_oauth_settle_sec)

    handle: str | None = None
    try:
        handle_loc = page.locator("text=/^@[A-Za-z0-9_]+/")
        if await handle_loc.count() > 0:
            t = await handle_loc.first.inner_text(timeout=1500)
            handle = t.strip().lstrip("@") or None
    except Exception:
        pass

    email_submitted = False
    for step in range(5):
        cur_url = page.url
        log.info("grail step %d, url=%s", step, cur_url)
        filled = await _fill_email_step(page, email)
        if filled:
            log.info("email submitted on step %d", step)
            email_submitted = True
            await asyncio.sleep(2)
            continue
        advanced = await _try_advance_step(page)
        if not advanced:
            log.info("no more buttons to click at %s", page.url)
            break
        if page.url == cur_url:
            break

    return GrailResult(
        x_connected=x_connected,
        email_submitted=email_submitted,
        final_url=page.url,
        handle=handle,
    )

async def claim_x_follow(ctx: BrowserContext) -> bool:
    """Клеймит 25 очков за фолловинг @fcgrails.

    1. Перехватывает сетевые запросы на /pass чтобы найти реальный API endpoint.
    2. Ищет кнопку Verify/Claim и кликает её.
    3. Если кнопка сделала POST — повторяем этот же запрос напрямую.
    """
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    captured_requests: list[str] = []

    def _on_request(req) -> None:
        url = req.url
        method = req.method
        if "grails.fancraze.com/api" in url and method in ("POST", "PUT", "PATCH"):
            captured_requests.append(f"{method} {url}")
            log.debug("captured API: %s %s", method, url)

    page.on("request", _on_request)

    try:
        await page.goto("https://grails.fancraze.com/pass",
                        wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)

        log.info("/pass loaded, url=%s", page.url)

        # Ищем кнопки Verify/Claim/Follow на странице
        claim_candidates = [
            page.get_by_role("button", name="Follow on X", exact=False),  # реальная кнопка
            page.get_by_role("button", name="Verify", exact=False),
            page.get_by_role("button", name="Claim", exact=False),
            page.get_by_role("button", name="Follow", exact=False),
            page.locator('button:has-text("Follow on X")'),
            page.locator('button:has-text("Verify")'),
            page.locator('button:has-text("Claim")'),
            page.locator('button:has-text("25")'),
        ]
        for loc in claim_candidates:
            try:
                target = loc.first
                if await target.is_visible(timeout=2000):
                    btn_text = await target.inner_text(timeout=1000)
                    log.info("found claim button: %r, clicking...", btn_text[:40])
                    await target.click(timeout=6000)
                    await asyncio.sleep(3)
                    # Проверяем что запрос был сделан
                    if captured_requests:
                        log.info("after click captured: %s", captured_requests[-1])
                        return True
            except Exception:
                continue

        # Если кнопку не нашли, пробуем прямые API endpoints
        known_endpoints = [
            "/api/tasks/x-follow",      # реальный endpoint (найден 2026-05-21)
            "/api/boosts/x-follow",
            "/api/boosts/xFollow",
            "/api/follow/claim",
        ]
        for endpoint in known_endpoints:
            try:
                resp = await page.request.post(
                    f"https://grails.fancraze.com{endpoint}",
                    headers={"Content-Type": "application/json"},
                    data="{}",
                )
                log.info("tried %s -> %d", endpoint, resp.status)
                if resp.status in (200, 201, 204):
                    log.info("xFollow claim OK via %s", endpoint)
                    return True
                if resp.status in (400, 409):
                    # 400/409 может означать "already claimed"
                    try:
                        body = await resp.json()
                        log.info("claim %s -> %d body=%s", endpoint, resp.status, str(body)[:100])
                        if "already" in str(body).lower() or "claimed" in str(body).lower():
                            return True
                    except Exception:
                        pass
                if resp.status == 404:
                    continue
            except Exception as e:
                log.debug("claim %s error: %s", endpoint, e)

        if captured_requests:
            log.info("all captured API calls: %s", captured_requests)
        else:
            log.warning("xFollow claim: no claim button found and no known endpoint worked")
    finally:
        page.remove_listener("request", _on_request)

    return False
