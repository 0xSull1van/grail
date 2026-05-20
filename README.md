# grail-bot

Прогоняет твиттер-акки через регу на grails.fancraze.com и фолловит указанный X-аккаунт.
Логин в твиттер идёт через куки (auth_token), браузер крутится на patchright (форк
playwright против анти-бота x.com), на каждый акк свой прокси и свой Chrome-профиль.

## Что делает

1. Берёт строку из accounts.txt, выдёргивает auth_token, цепляет прокси из proxies.txt
   (по индексу строки)
2. Поднимает Chrome через patchright с этим прокси, ставит auth_token на .x.com,
   проверяет что сессия твиттера живая
3. Открывает https://grails.fancraze.com/connect, жмёт Connect X
4. На x.com/oauth2/authorize жмёт Authorize app
5. Дожидается возврата на grails.fancraze.com (callback c кодом)
6. Если grail просит email, заполняет его (по дефолту генерит из логина:
   foo+grail0@gmail.com)
7. Идёт на x.com/<follow_handle> и фолловит
8. Закрывает браузер, пишет строку в results.csv, ждёт 180 секунд, берёт следующий акк

## Установка

Нужен Python 3.11+ и установленный Chrome (обычный, не Chromium).

```
pip install -r requirements.txt
patchright install chrome
```

Вторая команда докачивает Chrome-stealth-патчи. Если уже стоит обычный Chrome,
patchright использует его (channel="chrome" в коде).

## Настройка

Открыть config.toml. Минимум поменять:

- `follow_handle` — кого фолловить (по дефолту fcgrails)
- `delay_between_accounts_sec` — пауза между акками (по дефолту 180 секунд)
- `headless` — false если хочешь видеть окно браузера, true в фоне
- `email.strategy`:
  - `skip` — не заполнять email на 2-м шаге (рега будет неполной)
  - `auto_username` — собирать из логина: `<login>+grail<idx>@gmail.com`
  - `from_file` — читать построчно emails.txt

## Формат accounts.txt

Принимает три варианта, авто-детект:

1. Голый токен:
```
a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
```

2. Cookie-строка:
```
auth_token=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
auth_token=db056bc...; ct0=82f3...
```

3. Вендорский формат из evm/newtwitters.txt (поддерживается из коробки):
```
mylogin:mypassword:71234567890:us:01.01.2024:0:0:auth_token=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
```

В любом из вариантов можно после TAB указать прокси прямо в строке акка, тогда
proxies.txt можно вообще не трогать:
```
auth_token=db056bc...\thttp://user:pass@1.2.3.4:8080
```

## Формат proxies.txt

Поддерживаемые форматы строк:

1. Webshare-стиль (как из админки выгружается):
```
1.2.3.4:6195:proxyuser:proxypassword
```

2. URL с протоколом:
```
http://user:pass@1.2.3.4:8080
```

Прокси привязывается к аккам по индексу строки. Если прокси меньше чем акков,
циклится с начала.

## Запуск

```
python grail_bot.py
```

Опции:

```
python grail_bot.py --start 5         # начать с 6-го акка (skip 0..4)
python grail_bot.py --limit 3         # обработать только 3 акка
python grail_bot.py --start 5 --limit 1   # один акк, индекс 5
python grail_bot.py --dry-run         # парсинг + лог, без браузера
```

## Результаты

После каждого акка дописывается строка в results.csv. Колонки:

- idx, login_hint, proxy
- twitter_ok — пустили ли куки на /home
- grail_x_connected — дошли ли до callback после Authorize
- grail_email_submitted — заполнили ли email
- grail_final_url — где остановились
- handle — твиттер-хендл если grail его отрисовал
- follow_ok — кликнули ли Follow (или уже были подписаны)
- error — что упало, если упало
- started_at, finished_at

Полный лог пишется в grail-bot.log.

## Что может пойти не так

`twitter auth: X session check timed out` — куки протухли или прокси флагнут.
Замени токен или прокси.

`Connect X button not found on /connect` — grail обновил вёрстку. Открой /connect
руками, найди новую ссылку, поправь селектора в grail_runner.py
(функция run_grail_flow, секция connect_link_candidates).

`Authorize button not found` — x.com OAuth страница не загрузилась или прокси
завис. Проверь что прокси резидентный (с дата-центров x.com часто ловит Arkose
и не показывает Authorize, а сразу challenge).

`OAuth callback never returned to grail` — x.com отдал ошибку или редирект увёл
не туда. Запусти с `headless = false`, посмотри что на экране.

## Структура проекта

```
config.toml          конфиг
accounts.txt         токены твиттер-акков
proxies.txt          прокси
emails.txt           опционально, если strategy = "from_file"
grail_bot.py         CLI и оркестрация
twitter_client.py    логин в твиттер через куки + follow
grail_runner.py      клики по grail-шагам
accounts_loader.py   парсер accounts.txt и proxies.txt
profiles/            Chrome-профили (создаётся автоматически, не коммитить)
results.csv          результаты прогона
grail-bot.log        полный лог
```

## Безопасность

В репо не клади accounts.txt и proxies.txt с реальными токенами. В .gitignore они
уже исключены. Залить можно sample-файлы (accounts.sample.txt и proxies.sample.txt)
с одной строкой-примером.
