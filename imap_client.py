"""Async IMAP client for fetching Twitter email-confirmation codes.

Common providers auto-detected; corporate or self-hosted IMAP can be added
by passing an ImapProvider explicitly.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass

import aioimaplib

CODE_RE = re.compile(r"\b(\d[\d\s]{4,8}\d)\b")


@dataclass
class ImapProvider:
    host: str
    port: int = 993


_PROVIDERS: dict[str, ImapProvider] = {
    "gmail.com": ImapProvider("imap.gmail.com"),
    "googlemail.com": ImapProvider("imap.gmail.com"),
    "outlook.com": ImapProvider("imap-mail.outlook.com"),
    "hotmail.com": ImapProvider("imap-mail.outlook.com"),
    "live.com": ImapProvider("imap-mail.outlook.com"),
    "mail.ru": ImapProvider("imap.mail.ru"),
    "yandex.ru": ImapProvider("imap.yandex.ru"),
    "ya.ru": ImapProvider("imap.yandex.ru"),
    "rambler.ru": ImapProvider("imap.rambler.ru"),
    "gmx.com": ImapProvider("imap.gmx.com"),
    "gmx.net": ImapProvider("imap.gmx.com"),
    "gmx.us": ImapProvider("imap.gmx.com"),
    "mail.com": ImapProvider("imap.mail.com"),
    "usa.com": ImapProvider("imap.mail.com"),
    "firstmail.ltd": ImapProvider("imap.firstmail.ltd"),
}

# Vendors who sell Twitter accounts often use throwaway-mail domains routed
# through one upstream IMAP. Order26297831 batch and similar firstmail.ltd
# resells use these. List grows as we encounter them.
_FIRSTMAIL_DOMAINS = {
    "tenermail.com",
    "jugarmail.com",
    "floriamail.com",
    "supersphenymail.com",
}


def detect_provider(email: str) -> ImapProvider:
    domain = email.partition("@")[2].lower()
    if domain in _PROVIDERS:
        return _PROVIDERS[domain]
    if domain in _FIRSTMAIL_DOMAINS:
        return _PROVIDERS["firstmail.ltd"]
    raise ValueError(f"unknown IMAP provider for domain {domain!r}")


def extract_twitter_code(body: str) -> str | None:
    for m in CODE_RE.finditer(body):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 6:
            return digits
    return None


async def _try_login(
    prov: ImapProvider, email: str, password: str
) -> aioimaplib.IMAP4_SSL | None:
    """Connect + login. Returns connected client on success, None on auth fail."""
    client = aioimaplib.IMAP4_SSL(host=prov.host, port=prov.port, timeout=15)
    try:
        await client.wait_hello_from_server()
        res = await client.login(email, password)
        if res.result != "OK":
            try:
                await client.logout()
            except Exception:
                pass
            return None
        return client
    except Exception:
        try:
            await client.logout()
        except Exception:
            pass
        return None


async def fetch_latest_twitter_code(
    email: str,
    password: str,
    password_alt: str | None = None,
    provider: ImapProvider | None = None,
    timeout_sec: float = 60.0,
    poll_interval_sec: float = 5.0,
) -> str:
    """Connect, search for recent Twitter mail, return the 6-digit code.

    Tries `password` first, falls back to `password_alt` on auth fail. The
    fallback exists because some account vendors hand out a "primary" password
    that works for Twitter login but not for IMAP — IMAP needs the per-account
    password the vendor stores in a separate field.

    Raises TimeoutError on timeout.
    """
    prov = provider or detect_provider(email)
    deadline = asyncio.get_event_loop().time() + timeout_sec
    candidates = [password] + ([password_alt] if password_alt else [])
    working_password: str | None = None

    while asyncio.get_event_loop().time() < deadline:
        if working_password is None:
            for cand in candidates:
                client = await _try_login(prov, email, cand)
                if client is not None:
                    working_password = cand
                    try:
                        await client.logout()
                    except Exception:
                        pass
                    break
            if working_password is None:
                raise PermissionError(
                    f"IMAP auth failed for {email} on both primary "
                    f"and alt password ({prov.host})"
                )

        client = aioimaplib.IMAP4_SSL(host=prov.host, port=prov.port, timeout=15)
        try:
            await client.wait_hello_from_server()
            await client.login(email, working_password)
            await client.select("INBOX")
            res, data = await client.search('FROM "info@twitter.com"')
            if res != "OK":
                res, data = await client.search('FROM "verify@twitter.com"')
            ids: Iterable[str] = data[0].decode().split() if data and data[0] else []
            ids = sorted(ids, key=int, reverse=True)
            for msg_id in ids[:5]:
                _res, msg = await client.fetch(msg_id, "(RFC822)")
                if not msg:
                    continue
                body = b"".join(
                    p for p in msg if isinstance(p, (bytes, bytearray))
                ).decode("utf-8", errors="replace")
                code = extract_twitter_code(body)
                if code:
                    return code
        finally:
            try:
                await client.logout()
            except Exception:
                pass
        await asyncio.sleep(poll_interval_sec)

    raise TimeoutError(f"no Twitter code in inbox within {timeout_sec}s")
