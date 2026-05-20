"""Twitter login automation via patchright + real Chrome.

Two modes:
  - cred: login + password + (optional) email-code challenge.
  - cookie: inject auth_token + ct0, validate session.

Why patchright (not vanilla playwright): x.com's anti-bot reads CDP
`Runtime.enable` leaks and `navigator.webdriver`; vanilla Playwright fails
the `task.json` integrity check silently — the username submit appears to
do nothing. patchright patches those leaks + drives real Chrome (matching
JA3 TLS fingerprint).

Both modes return a BrowserContext (from launch_persistent_context) that
the caller (KYA runner) reuses to drive kya.link in the same session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from patchright.async_api import BrowserContext, async_playwright

from imap_client import fetch_latest_twitter_code

# Heading keywords that mean "we sent you a code, type it" — needs IMAP fetch.
# Otherwise the challenge is the alt-identifier prompt: type the email itself.
_CODE_CHALLENGE_KEYWORDS = (
    "code", "verification", "verify", "we sent",
    "код", "проверочный", "подтверждения",
)

# Heading keywords for the phone-only security check Twitter throws at fresh
# logins from new IPs ("Help us keep your account secure / Phone number"). The
# input expects digits with country code; alt-id branch (which types email)
# silently fails this check, so we type CredAccount.phone instead.
_PHONE_CHALLENGE_KEYWORDS = (
    "phone number", "secure", "keep your account",
    "помогите", "безопас", "номер телефона",
)


class TwitterLoginError(Exception):
    pass


async def _click_next(page, timeout_ms: int = 30000) -> None:
    """Кликает кнопку "Next / Далее / Siguiente" на страницах логина X.
    Пробует несколько локаторов и JS-click для надёжности."""
    candidates = [
        page.locator('button[data-testid="ocfLoginNextButton"]'),
        page.locator('button[data-testid="LoginButton"]'),
        page.get_by_role("button", name="Next", exact=True),
        page.get_by_role("button", name="Далее", exact=True),
        page.get_by_role("button", name="Siguiente", exact=True),
        page.get_by_role("button", name="Avanti", exact=True),
        page.get_by_role("button", name="次へ", exact=True),
        # Fallback — любая primary кнопка на странице логина
        page.locator("button[type=submit]"),
    ]
    for loc in candidates:
        try:
            target = loc.first
            if await target.is_visible(timeout=2000):
                try:
                    await target.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    await target.evaluate("n => n.click()")
                except Exception:
                    await target.click(timeout=timeout_ms)
                return
        except Exception:
            continue
    raise TwitterLoginError("Next button not found (tried multiple locators)")


async def _click_login_btn(page, timeout_ms: int = 30000) -> None:
    """Кликает "Log in / Войти" на странице ввода пароля."""
    candidates = [
        page.locator('button[data-testid="LoginButton"]'),
        page.get_by_role("button", name="Log in", exact=True),
        page.get_by_role("button", name="Войти", exact=True),
        page.get_by_role("button", name="Iniciar sesión", exact=True),
        page.get_by_role("button", name="Accedi", exact=True),
        page.get_by_role("button", name="ログイン", exact=True),
        page.locator("button[type=submit]"),
    ]
    for loc in candidates:
        try:
            target = loc.first
            if await target.is_visible(timeout=2000):
                try:
                    await target.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    await target.evaluate("n => n.click()")
                except Exception:
                    await target.click(timeout=timeout_ms)
                return
        except Exception:
            continue
    raise TwitterLoginError("Log in button not found")



@dataclass
class CredAccount:
    login: str
    password: str
    proxy_url: str | None
    email: str = ""
    email_password: str = ""
    email_password_alt: str | None = None
    phone: str | None = None


@dataclass
class CookieAccount:
    cookies_header: str  # "auth_token=...; ct0=..."
    proxy_url: str | None


import time as _time

# Cookie attributes matching what the X Token Login chrome extension sets via
# document.cookie. Notably:
#   - httpOnly MUST be False — Twitter's frontend JS reads auth_token from
#     document.cookie to determine auth state. httpOnly=True hides it.
#   - sameSite=Lax — default browser-set. None breaks the handshake on x.com.
#   - 1-year expiry to match extension behaviour.
# We do NOT auto-generate ct0: Twitter's own JS mints ct0 from the server
# response after auth_token is validated. Our own ct0 gets overwritten and
# any API calls in the meantime 403 with mismatched X-Csrf-Token.
_COOKIE_ATTRS = {
    "auth_token": {"httpOnly": False, "secure": True, "sameSite": "Lax"},
    "ct0":        {"httpOnly": False, "secure": True, "sameSite": "Lax"},
    "kdt":        {"httpOnly": False, "secure": True, "sameSite": "Lax"},
    "twid":       {"httpOnly": False, "secure": True, "sameSite": "Lax"},
    "guest_id":   {"httpOnly": False, "secure": True, "sameSite": "Lax"},
    "personalization_id": {"httpOnly": False, "secure": True, "sameSite": "Lax"},
}


def _parse_cookies_header(
    header: str, domains: tuple[str, ...] = (".x.com",)
) -> list[dict]:
    """Parse 'auth_token=...; ct0=...' into Playwright cookie dicts.

    Replicates the X Token Login extension's behaviour: each cookie set on
    `.x.com` only, non-httpOnly, sameSite=Lax, 1-year expiry. If only
    auth_token is in the input, no ct0 is set — Twitter's frontend JS will
    fetch one on first authenticated request.
    """
    parsed: dict[str, str] = {}
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        parsed[name.strip()] = value.strip()

    expires = int(_time.time()) + 365 * 24 * 3600
    out: list[dict] = []
    for name, value in parsed.items():
        attrs = _COOKIE_ATTRS.get(
            name, {"httpOnly": False, "secure": True, "sameSite": "Lax"}
        )
        for domain in domains:
            out.append({
                "name": name, "value": value,
                "domain": domain, "path": "/",
                "expires": expires,
                **attrs,
            })
    return out


async def _proxy_kwargs(proxy_url: str | None) -> dict:
    """Split inline-auth proxy URL into Playwright's separate fields.

    Chromium does NOT URL-decode the password from the inline form, so
    `http://u:p%2Bq@host` ends up sending literal `p%2Bq` as the password
    and the proxy returns 407. We decode here so the raw password reaches
    Chromium's Basic-auth header.
    """
    if not proxy_url:
        return {}
    if "@" in proxy_url:
        from urllib.parse import unquote
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


async def _launch_chrome(
    proxy_url: str | None, profile_dir: Path, headless: bool
) -> BrowserContext:
    """patchright + real Chrome + persistent profile per agent.

    Persistent profile per agent means cookies survive between runs (less
    re-login churn) and Twitter sees a "real-looking" browser fingerprint.
    Do NOT pass user_agent / viewport / args — patchright handles spoofing
    and overrides break it.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    proxy_kw = await _proxy_kwargs(proxy_url)
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=headless,
        **proxy_kw,
    )


async def login_with_creds(
    account: CredAccount, profile_dir: Path, headless: bool = True
) -> BrowserContext:
    ctx = await _launch_chrome(account.proxy_url, profile_dir, headless)
    page = await ctx.new_page()

    # Pre-warm: visit x.com root before /i/flow/login. Without this, Twitter's
    # JS bundle sometimes returns "Something went wrong. Try reloading." on
    # cold proxy + login page combo. The pre-warm primes guest_id cookies on
    # the residential IP and lets Cloudflare hand out a fresh JS token.
    try:
        await page.goto("https://x.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    except Exception:
        pass

    # On fresh residential IPs x.com shows an EU cookie consent banner that
    # blocks the onboarding/task.json call from firing — login modal hangs on
    # spinner forever until consent is given. Click "Accept all" if visible.
    async def _dismiss_cookie_banner() -> None:
        try:
            for label in ("Accept all cookies", "Refuse non-essential cookies"):
                btn = page.get_by_role("button", name=label)
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await asyncio.sleep(1.5)
                    return
        except Exception:
            pass

    await _dismiss_cookie_banner()

    username_input = page.locator('input[autocomplete="username"]')

    # Try login page up to 4 times. If Twitter renders "Something went wrong",
    # click Retry; if Retry is also stuck, do a hard reload.
    for attempt in range(4):
        try:
            await page.goto(
                "https://x.com/i/flow/login",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception:
            await asyncio.sleep(2)
            continue
        # Banner may re-appear on the login URL, dismiss again.
        await _dismiss_cookie_banner()
        try:
            await username_input.wait_for(timeout=15000)
            break
        except Exception:
            pass
        # Username didn't appear. Look for the Retry button on the error card.
        try:
            retry_btn = page.get_by_role("button", name="Retry")
            if await retry_btn.count() > 0:
                await retry_btn.first.click()
                await asyncio.sleep(3)
                if await username_input.count() > 0:
                    try:
                        await username_input.wait_for(timeout=6000)
                        break
                    except Exception:
                        pass
        except Exception:
            pass
        # Hard reload before the next attempt — fresh navigation often
        # bypasses the transient JS-bundle hiccup.
        try:
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            try:
                await username_input.wait_for(timeout=6000)
                break
            except Exception:
                pass
        except Exception:
            pass
        await asyncio.sleep(2)
    else:
        await ctx.close()
        raise TwitterLoginError(
            "twitter login page never rendered username input "
            "(proxy/IP may be blocked or rate-limited by X)"
        )

    # 1. username — type with keystroke delay so Twitter sees realistic events
    # Capture task.json response so we can see anti-fraud rejection codes when
    # the form silently re-renders ("flow reset" pattern on flagged IPs).
    task_responses: list[tuple[int, str]] = []

    async def _capture_task(resp) -> None:
        try:
            url = resp.url
            if "/onboarding/task.json" in url or "/i/api/1.1/onboarding" in url:
                body = ""
                try:
                    body = (await resp.text())[:400]
                except Exception:
                    pass
                task_responses.append((resp.status, body))
                print(f"[task.json] {resp.status} {url[:90]} :: {body[:200]}")
        except Exception:
            pass

    page.on("response", lambda r: asyncio.create_task(_capture_task(r)))

    def _check_anti_fraud_reject() -> None:
        """Scan recent task.json responses; raise immediately on code 399
        ("Could not log you in now") so we don't wait 30s for /home."""
        for status, body in task_responses[-5:]:
            if status >= 400 and '"code":399' in body:
                raise TwitterLoginError(
                    f"X anti-fraud reject (code 399) — account or IP flagged. "
                    f"task.json: {body[:200]}"
                )

    await username_input.type(account.login, delay=80)
    await asyncio.sleep(0.7)
    await _click_next(page)
    # Give task.json a chance to land before challenge/password lookup.
    await asyncio.sleep(2.5)
    _check_anti_fraud_reject()

    # Detect "form reset" — username field reappeared instead of advancing.
    # Means anti-fraud silently rejected the flow. Surface explicit error
    # rather than letting the password wait_for time out 15s later.
    try:
        if await username_input.count() > 0 and await username_input.first.is_visible(timeout=500):
            recent = task_responses[-3:] if task_responses else []
            await ctx.close()
            raise TwitterLoginError(
                f"login flow reset after username submit — anti-fraud likely "
                f"flagged IP/device. last task.json responses: {recent}"
            )
    except TwitterLoginError:
        raise
    except Exception:
        pass

    # 2. Twitter shows three distinct challenges between username and password:
    #   (a) Phone-only security check: "Help us keep your account secure /
    #       Phone number" — input uses autocomplete="tel" or inputmode="tel"
    #       (NOT data-testid="ocfEnterTextTextInput"). Needs registered phone.
    #   (b) Alt-identifier: "Enter your phone or email" (anti-fraud check on
    #       first login from a new IP) — input uses ocfEnterTextTextInput.
    #       Prefer phone if we have it, else email.
    #   (c) Email code: "We sent a verification code to ...". Fetch via IMAP.
    # Distinguish by heading text. Loop a few times since multiple challenges
    # can chain (e.g. phone-check then email-code) before password.
    def _norm_phone(p: str) -> str:
        return p if p.startswith("+") else "+" + p

    challenge_selector = (
        'input[data-testid="ocfEnterTextTextInput"], '
        'input[autocomplete="tel"], '
        'input[autocomplete="tel-national"], '
        'input[inputmode="tel"], '
        'input[type="tel"]'
    )

    for _ in range(4):
        # Stop if password field is already visible — challenge phase is over.
        try:
            if await page.locator('input[name="password"]').count() > 0:
                if await page.locator('input[name="password"]').first.is_visible(timeout=500):
                    break
        except Exception:
            pass
        try:
            challenge_input = page.locator(challenge_selector).first
            await challenge_input.wait_for(timeout=5000)
        except Exception:
            break  # no challenge surface — proceed to password
        try:
            heading = (await page.locator("h1").first.inner_text(timeout=2000)).lower()
        except Exception:
            heading = ""
        is_code = any(kw in heading for kw in _CODE_CHALLENGE_KEYWORDS)
        is_phone = any(kw in heading for kw in _PHONE_CHALLENGE_KEYWORDS)
        if is_code:
            if not account.email or not account.email_password:
                raise TwitterLoginError(
                    "email-code challenge fired but cred record has no email "
                    "(re-import this twitter with email + email_password)"
                )
            value = await fetch_latest_twitter_code(
                account.email, account.email_password,
                password_alt=account.email_password_alt,
            )
        elif is_phone and account.phone:
            value = _norm_phone(account.phone)
        elif account.phone:
            # alt-identifier ("phone or email") — phone preferred when present
            value = _norm_phone(account.phone)
        elif account.email:
            value = account.email
        else:
            raise TwitterLoginError(
                f"challenge fired (heading={heading!r}) but cred record "
                "has neither phone nor email"
            )
        await challenge_input.type(value, delay=80)
        await asyncio.sleep(0.5)
        await _click_next(page)
        await asyncio.sleep(1.5)  # let Twitter re-render

    # 3. password
    pwd_input = page.locator('input[name="password"]')
    await pwd_input.wait_for(timeout=15000)
    await pwd_input.type(account.password, delay=80)
    await asyncio.sleep(0.7)
    await _click_login_btn(page)
    # 399 frequently fires AFTER password submit (X validates pwd, then runs
    # final risk check). Catch fast instead of waiting 30s on /home below.
    await asyncio.sleep(2.5)
    _check_anti_fraud_reject()

    # 4. Post-password challenges. Twitter ALWAYS shows LoginAcid task on
    # cold devices/IPs. Possible variants:
    #   - "Help us keep your account safe" / "Phone number" (phone-only input,
    #     hint "ending in NN") — refill with stored phone
    #   - "We sent a verification code to ..." (email-code, ocfEnterTextTextInput)
    #     — pull from IMAP
    #   - "Enter your phone or email" (alt-id) — prefer phone if we have it
    # Loop a couple of times since email-code can chain after phone.
    post_challenge_selector = (
        'input[data-testid="ocfEnterTextTextInput"], '
        'input[autocomplete="tel"], '
        'input[autocomplete="tel-national"], '
        'input[inputmode="tel"], '
        'input[type="tel"]'
    )
    for _ in range(3):
        # Already on /home? Done.
        try:
            if "/home" in page.url:
                break
        except Exception:
            pass
        try:
            challenge_input = page.locator(post_challenge_selector).first
            await challenge_input.wait_for(timeout=5000)
        except Exception:
            break  # no further challenge — fall through to /home wait
        try:
            heading = (await page.locator("h1").first.inner_text(timeout=2000)).lower()
        except Exception:
            heading = ""
        is_code = any(kw in heading for kw in _CODE_CHALLENGE_KEYWORDS)
        is_phone = any(kw in heading for kw in _PHONE_CHALLENGE_KEYWORDS)
        if is_code:
            if not account.email or not account.email_password:
                raise TwitterLoginError(
                    "post-password email-code challenge fired but cred record "
                    "has no email (heading=" + repr(heading) + ")"
                )
            value = await fetch_latest_twitter_code(
                account.email, account.email_password,
                password_alt=account.email_password_alt,
            )
        elif is_phone and account.phone:
            value = _norm_phone(account.phone)
        elif account.phone:
            # alt-id "phone or email" — phone wins when present
            value = _norm_phone(account.phone)
        elif account.email:
            value = account.email
        else:
            raise TwitterLoginError(
                f"post-password challenge (heading={heading!r}) but cred "
                "record has neither phone nor email"
            )
        await challenge_input.type(value, delay=80)
        await asyncio.sleep(0.5)
        await _click_next(page)
        await asyncio.sleep(2.0)

    # 5. wait for /home
    try:
        await page.wait_for_url("**/home", timeout=30000)
    except Exception as e:
        raise TwitterLoginError(f"login did not land on /home: {e}") from e

    return ctx


async def login_with_cookies(
    account: CookieAccount, profile_dir: Path, headless: bool = True
) -> BrowserContext:
    ctx = await _launch_chrome(account.proxy_url, profile_dir, headless)
    cookies = _parse_cookies_header(account.cookies_header)
    await ctx.add_cookies(cookies)
    page = await ctx.new_page()

    # Pre-warm at x.com root before /home — gives the page JS a chance to
    # run on a low-friction surface before requesting the authenticated
    # home shell. Without this, Arkose triggers more often.
    try:
        await page.goto("https://x.com", wait_until="domcontentloaded")
        await asyncio.sleep(2)
    except Exception:
        pass

    await page.goto("https://x.com/home", wait_until="domcontentloaded")

    # Detect verification challenges before waiting on the home tabbar.
    if any(s in page.url for s in ("arkose", "/i/flow/", "consent_flow")):
        try:
            from pathlib import Path as _P
            dbg = _P("debug-shots")
            dbg.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(dbg / "cookie-check-fail.png"))
        except Exception:
            pass
        await ctx.close()
        raise TwitterLoginError(
            f"verification challenge triggered (URL={page.url}) — "
            "use residential proxy or no proxy. Debug: ~/.orch/kya-debug/cookie-check-fail.png"
        )
    if "/login" in page.url or "/i/flow/login" in page.url:
        await ctx.close()
        raise TwitterLoginError("cookie session is dead (redirected to login)")

    # Snapshot immediately so we have a screenshot even if the operator
    # closes the Chrome window before the wait_for below times out.
    from pathlib import Path as _P
    dbg = _P("debug-shots")
    dbg.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(dbg / "cookie-check-snapshot.png"))
        (dbg / "cookie-check-snapshot.html").write_text(
            await page.content(), encoding="utf-8"
        )
    except Exception:
        pass

    # Twitter renders different shells on /, /home, /explore — but ALL of
    # them show the side-nav AccountSwitcher (avatar at the bottom-left)
    # when the session is authenticated. AppTabBar_Home_Link is /home-only.
    # Check several selectors and accept the first one that resolves.
    auth_signals = [
        '[data-testid="SideNav_AccountSwitcher_Button"]',
        '[data-testid="AppTabBar_Home_Link"]',
        '[data-testid="primaryColumn"]',
        'a[href="/home"][aria-label="Home"]',
    ]
    deadline = 90.0
    elapsed = 0.0
    found = False
    while elapsed < deadline:
        for sel in auth_signals:
            try:
                if await page.locator(sel).first.is_visible(timeout=1000):
                    found = True
                    break
            except Exception:
                continue
        if found:
            break
        await asyncio.sleep(2)
        elapsed += 2

    if not found:
        try:
            await page.screenshot(path=str(dbg / "cookie-check-fail.png"))
            (dbg / "cookie-check-fail.html").write_text(
                await page.content(), encoding="utf-8"
            )
        except Exception:
            pass
        await ctx.close()
        raise TwitterLoginError(
            f"cookies did not authenticate within 90s (URL was {page.url}). "
            "Debug: ~/.orch/kya-debug/cookie-check-snapshot.png (initial) and "
            "cookie-check-fail.png (final)"
        )
    return ctx
