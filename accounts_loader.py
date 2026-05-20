"""Загрузка твиттер-аккаунтов из текстовика.

Поддерживаемые форматы строк (auto-detected по количеству и виду полей):

  1. Cookie-mode (только auth_token + опц. ct0):
       auth_token=XXX
       auth_token=XXX; ct0=YYY
       (любой из выше) + \t + proxy_url

  2. Vendor cookie-формат (как newtwitters.txt / work1.txt / work2.txt):
       login:pass:phone:country:date:tweets:subs:auth_token=XXX
     В нём есть login+password но НЕТ email — для cred-mode нужен email,
     поэтому такие акки идут как Cookie (используя auth_token).

  3. Cred-mode (Order26297831 формат):
       login:pass:email:emailpass:phone:country:date:tweets:subs:auth_token=XXX
     Здесь есть всё — можно делать cred-mode логин с прохождением challenge
     через IMAP email-код.

  4. Любая строка + TAB + proxy:
       <line>\thttp://user:pass@host:port

Прокси-файл (опционально) маппится 1:1 по индексу строки, если inline-прокси нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Импорт типов из twitter_client (evm-копия)
from twitter_client import CookieAccount, CredAccount


@dataclass
class Account:
    """Универсальный контейнер. cred имеет приоритет над cookie если оба заданы."""
    cred: CredAccount | None
    cookie: CookieAccount | None
    proxy_url: str | None
    login_hint: str | None


_AUTH_TOKEN_RE = re.compile(r"\bauth_token=([0-9a-fA-F]{30,})")
_CT0_RE = re.compile(r"\bct0=([0-9a-fA-F]+)")
_RAW_HEX_RE = re.compile(r"^[0-9a-fA-F]{32,80}$")
_EMAIL_RE = re.compile(r"^[\w.+\-]+@[\w.-]+\.[A-Za-z]{2,}$")


def _proxy_from_webshare(s: str) -> str:
    parts = s.strip().split(":")
    if len(parts) != 4:
        raise ValueError(f"bad webshare proxy: {s!r}")
    ip, port, user, pw = parts
    return f"http://{user}:{pw}@{ip}:{port}"


def parse_proxies(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            out.append(line)
        else:
            out.append(_proxy_from_webshare(line))
    return out


def _is_phone(s: str) -> bool:
    s = s.strip().lstrip("+")
    return s.isdigit() and 7 <= len(s) <= 15


def _is_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s.strip()))


def _strip_auth_token_field(parts: list[str]) -> tuple[list[str], str | None, str | None]:
    """Найти последний токен auth_token=...|ct0=... и убрать его из parts.
    Возвращает (clean_parts, auth_token, ct0_or_None)."""
    auth_token = None
    ct0 = None
    new_parts = []
    for p in parts:
        m = _AUTH_TOKEN_RE.search(p)
        if m:
            auth_token = m.group(1)
            mc = _CT0_RE.search(p)
            if mc:
                ct0 = mc.group(1)
            continue
        mc = _CT0_RE.search(p)
        if mc and ct0 is None:
            ct0 = mc.group(1)
            continue
        new_parts.append(p)
    return new_parts, auth_token, ct0


def parse_account_line(line: str) -> Account:
    """Разбирает строку в Account с заполненными cred/cookie/proxy."""
    line = line.rstrip("\n").rstrip("\r")
    inline_proxy: str | None = None
    if "\t" in line:
        line, inline_proxy = line.split("\t", 1)
        inline_proxy = inline_proxy.strip() or None
    raw = line.strip()
    if not raw:
        raise ValueError("empty line")

    # Cookie-строка без двоеточий-разделителей (только auth_token=... [;ct0=...])
    if raw.startswith("auth_token=") or _RAW_HEX_RE.match(raw):
        cookies_header = raw if raw.startswith("auth_token=") else f"auth_token={raw}"
        return Account(
            cred=None,
            cookie=CookieAccount(cookies_header=cookies_header, proxy_url=inline_proxy),
            proxy_url=inline_proxy,
            login_hint=None,
        )

    # Двоеточный формат: разбираем
    parts = raw.split(":")
    clean, auth_token, ct0 = _strip_auth_token_field(parts)

    if len(clean) < 2:
        raise ValueError(f"can't parse line (too few fields): {raw[:80]!r}")

    login = clean[0]
    password = clean[1]
    email: str | None = None
    email_password: str | None = None
    phone: str | None = None

    # Если parts[2] это email и parts[3] это его пароль — cred-формат (Order...)
    if len(clean) >= 4 and _is_email(clean[2]):
        email = clean[2]
        email_password = clean[3]
        rest = clean[4:]
        if rest and _is_phone(rest[0]):
            phone = rest[0]
    else:
        # Vendor cookie-формат: login:pass:phone:country:date:tweets:subs:auth_token
        rest = clean[2:]
        if rest and _is_phone(rest[0]):
            phone = rest[0]

    cookies_header: str | None = None
    if auth_token:
        cookies_header = f"auth_token={auth_token}"
        if ct0:
            cookies_header += f"; ct0={ct0}"

    cred: CredAccount | None = None
    # Создаём CredAccount если есть login+pass (даже без email — Twitter принимает phone)
    if login and password:
        cred = CredAccount(
            login=login,
            password=password,
            proxy_url=inline_proxy,
            email=email or "",
            email_password=email_password or "",
            phone=phone,
        )

    cookie: CookieAccount | None = None
    if cookies_header:
        cookie = CookieAccount(cookies_header=cookies_header, proxy_url=inline_proxy)

    if cred is None and cookie is None:
        raise ValueError(f"no creds and no auth_token in line: {raw[:80]!r}")

    return Account(
        cred=cred,
        cookie=cookie,
        proxy_url=inline_proxy,
        login_hint=login,
    )


def load_accounts(accounts_path: Path, proxies_path: Path | None) -> list[Account]:
    proxies: list[str] = []
    if proxies_path and proxies_path.exists():
        proxies = parse_proxies(proxies_path)

    accounts: list[Account] = []
    line_idx = 0
    for raw in accounts_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        acc = parse_account_line(raw)
        if acc.proxy_url is None and proxies:
            px = proxies[line_idx % len(proxies)]
            acc.proxy_url = px
            if acc.cred:
                acc.cred.proxy_url = px
            if acc.cookie:
                acc.cookie.proxy_url = px
        accounts.append(acc)
        line_idx += 1
    return accounts
