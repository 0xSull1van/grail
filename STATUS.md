# Текущее состояние тестов

Прогон от 2026-05-20:

## Что работает

- Парсер собрал 195 уникальных акков (cookies.txt 50 + Order26297831 25 + newtwitters 20 + work1 75 + work2 25, дедуп по auth_token)
- Из них 25 в cred-формате (login+pass+email+emailpass+phone, Order26297831)
- 195 в cookie-формате (auth_token, у 50 ещё и ct0)
- Парсер прокси: Webshare (50), Oxylabs (50), Oxylabs NL (1)
- twitter_client.py — 1-в-1 копия из evm/ardinals-orchestrator (543 строки, login_with_creds + login_with_cookies + IMAP)
- twitter_session.py — обёртка с launch + cred/cookie выбор + follow
- grail_runner.py — клики по grail UI (Connect X / Authorize / email)
- grail_bot.py — оркестратор с `--mode auto/cred/cookie`, `--no-proxy`, `--skip-delay-on-fail`

## Что не работает (data-проблемы, не code)

1. **Прокси Oxylabs (residential)** — возвращают `ERR_HTTP_RESPONSE_CODE_FAILURE`.
   Скорее всего subscription expired. Нужны новые credentials.

2. **Прокси Webshare (datacenter)** — Twitter сразу даёт `/account/access` Cloudflare
   challenge на cookie-mode. Cred-mode не доходит даже до login form (Twitter
   блокирует datacenter IP на login flow).

3. **Cookies в cookies.txt** — протухли. Куки из вендорской поставки 2022-2024
   годов: либо session expired, либо акк локнут.

4. **Cookies в Order26297831** — pre-warm проходит без challenge, но `/home`
   рендерит logged-out shell — куки тоже мертвы.

## Что делать чтобы реально прогнать

Минимум **одно** из:

### Вариант А: свежие residential прокси

Купить новые Oxylabs / Bright Data / IPRoyal residential. Положить в `proxies.txt`
(одна строка на прокси, формат `http://user:pass@host:port` или Webshare-стиль
`ip:port:user:pass`). После этого:
```
python grail_bot.py --mode cookie  # для аккаунтов с auth_token (195)
```

### Вариант Б: свежие cookies

Открыть Twitter в браузере на каждом нужном акке, выгрузить cookies через
расширение типа "X Token Login" / "Cookie-Editor". Положить в `accounts.txt`
по строке на акк в формате:
```
auth_token=XXX; ct0=YYY\thttp://user:pass@proxy:port
```
(TAB между cookies и прокси)

Затем:
```
python grail_bot.py --mode cookie
```

### Вариант В: cred-mode (login+pass+IMAP email-code)

Только для Order26297831 акков (у них есть email+emailpass на firstmail.ltd
резеллах). Нужен:
1. Рабочий residential прокси
2. Или хотя бы IP который Twitter не флагнул на login flow

```
python grail_bot.py --mode cred --proxies-file proxies.txt
```

Cred-mode пройдёт через `/i/flow/login` форму, password challenge, IMAP пуллинг
email-кода с firstmail. На каждый акк нужны ~2-3 минуты.

## Технический долг

- Resource warnings про unclosed transport (`I/O operation on closed pipe`) на
  Windows — асинхронная очистка patchright. Не блокирует, но шумно. Можно
  игнорировать `ResourceWarning` через filter в `grail_bot.py` setup_logging.
- Headless=false по дефолту — открывает видимое окно Chrome. Юзеру удобно
  смотреть что происходит. Для прогонки на сервере поставить headless=true в
  config.toml.
