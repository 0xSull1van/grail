# grail-bot

Прогоняет твиттер-акки через регу на grails.fancraze.com:
1. Логин в твиттер (login+pass через challenge IMAP / phone)
2. Connect X через OAuth + Authorize
3. Email шаг + landing на /pass
4. Follow @fcgrails
5. Клейм 25 очков за follow на /pass странице
6. Случайная задержка 40-80 мин и следующий акк

Система рефералов: каждый код используется 5-10 акков, потом ротация. После каждой
успешной реги новый код этого акка тянется через `/api/me` и добавляется в пул.

## Установка

Нужен Python 3.11+ и установленный Chrome.

```
pip install -r requirements.txt
patchright install chrome
```

## Настройка

1. `config.toml` — основные параметры (handle для фоллова, тайминги, режим email)
2. `referrals.local.toml` (создать самому, в gitignore) — твой базовый реф-код:

```toml
[referrals]
base_code = "TWOIREFKOD"
```

3. `accounts.txt` — твиттер-акки. Собирается через `build_accounts.py` из исходников
   в evm проекте, или клади свои строки:

```
# Формат cred-mode (login:pass:email:emailpass:phone:country:date:tweets:subs:auth_token=XXX)
mylogin:mypass:mail@gmail.com:mailpass:71234567890:us:01.01.2024:0:0:auth_token=abc...

# Формат cookie-mode (только auth_token)
auth_token=abc...
auth_token=abc...; ct0=def...

# Любой формат + TAB + proxy
auth_token=abc...	http://user:pass@host:port
```

4. `proxies.txt` — прокси (опционально). Формат Webshare `ip:port:user:pass` или
   `http://user:pass@host:port`. Маппинг 1:1 по индексу акка.

## Запуск

```
# Все оставшиеся акки начиная с индекса N
python grail_bot.py --mode cred --start 45 --no-proxy --skip-delay-on-fail

# Только N акков для теста
python grail_bot.py --mode cred --start 45 --limit 5 --no-proxy --skip-delay-on-fail

# Через прокси (Webshare datacenter в proxies.txt)
python grail_bot.py --mode cred --start 45 --skip-delay-on-fail

# Своя проксь
python grail_bot.py --mode cred --start 45 --proxies-file my_proxies.txt --skip-delay-on-fail

# Только парсинг + проверка пула рефов, без браузера
python grail_bot.py --dry-run --start 45 --limit 5
```

Опции:
- `--mode cred|cookie|auto` — cred логинится по логину+паролю, cookie ставит куки
- `--start N` — начать с какого индекса (0-based)
- `--limit N` — сколько обработать (0 = все)
- `--no-proxy` — игнорировать прокси
- `--proxies-file path` — свой proxy-файл
- `--skip-delay-on-fail` — не ждать 40-80 мин если twitter_ok=False
- `--dry-run` — парсинг без браузера

## Проверка результатов

```
python check_results.py             # таблица всех акков
python check_results.py --summary   # только итоговые цифры
python check_results.py --ok        # только успешные
python check_results.py --failed    # только провалившиеся
```

## Сбор accounts.txt из исходников evm-проекта

```
python build_accounts.py           # дефолт: Order + work1 + work2 = 125
python build_accounts.py --list    # счётчики по каждому файлу
python build_accounts.py --all     # все 5 файлов (195 уникальных)
python build_accounts.py --sources cookies.txt work1.txt  # custom набор
```

## Где смотреть что происходит

- `grail-bot.log` — полный лог выполнения (все шаги + ошибки)
- `results.csv` — результат каждого акка построчно (CSV)
- `referrals.json` — текущее состояние пула рефералов (gitignored)
- `profiles/acc-XXX/` — Chrome-профили на каждый акк (cookies сохраняются)
- `debug-shots/` — скриншоты при таймаутах (gitignored)

## Структура CSV results.csv

| колонка | значение |
|---|---|
| idx | индекс акка в accounts.txt |
| login_hint | логин акка |
| proxy | использованный прокси |
| mode_used | cred / cookie / none |
| twitter_ok | 1 = залогинились в твиттер |
| grail_x_connected | 1 = прошёл OAuth на grail |
| grail_email_submitted | 1 = ввёл email |
| grail_final_url | где остановился (/pass = успех) |
| handle | твиттер-хендл от grail |
| follow_ok | 1 = подписан на @fcgrails |
| xfollow_claimed | 1 = заклеймил 25 очков |
| ref_used | реф-код, по которому регался этот акк |
| new_ref_code | реф-код этого акка для будущих регов |
| error | если упал — что упало |

## Что может пойти не так

- `code 399` — Twitter anti-fraud. Акк или IP флагнут. Чаще на старых акках из
  high-risk стран (Индия, Эфиопия, Танзания).
- `account/access challenge` — Cloudflare challenge от Twitter на datacenter
  proxy. Используй residential или без прокси.
- `cookies did not authenticate` — куки протухли. Перейди в cred-mode.
- `Connect X button not found` — после успешной реги акк уже зарегистрирован,
  /connect показывает другое.

## Безопасность

В gitignore: `accounts.txt`, `proxies.txt`, `referrals.json`, `referrals.local.toml`,
`results.csv`, `profiles/`, `debug-shots/`, `grail-bot.log`. Реальные акки и реф-коды
в репо не попадают.
