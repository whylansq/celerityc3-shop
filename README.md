# VPN Shop Bot

Отдельный Telegram-бот для ручной продажи VPN-доступа через существующий C³
CELERITY. Бот работает через Telegram long polling: он не поднимает webhook и
не публикует HTTP-порты.

## Границы проекта

- Не использует Compose, БД, Redis, MongoDB или код основного операционного бота.
- Не подключается к внутренним контейнерам Celerity.
- Использует только официальный REST API Celerity.
- Для нового бота нужен отдельный API key Celerity.
- В проекте нет Stars, Telegram Payments, банковских API, OCR и автопроверки
  переводов.
- Единственная нода, которую предполагает интерфейс подключения, — Финляндия.

## API Celerity

Контракт сверен с основным операционным ботом:

- `GET /api/users` — поиск пользователя;
- `POST /api/users` — создание с полями `userId`, `enabled`, `expireAt`;
- `PUT /api/users/:userId` — продление и включение;
- ответ пользователя содержит `subscriptionToken`;
- ссылка подписки имеет вид `/api/files/:token`.

Для этого бота достаточно scopes `users:read` и `users:write`. MCP, nodes,
probes и SSH ему не нужны. Для авторизации по умолчанию используется
`Authorization: Bearer ...`; при необходимости это меняется в
`CELERITY_AUTH_MODE`.

## Локальная проверка

```bash
cp .env.example .env
# заполнить .env только локально или на сервере
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

Без заполненных секретов бот специально не запускается.

## Запуск на VPS

Сначала на VPS сделайте обязательную безопасную проверку:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
ss -ltnp
```

Разместите этот каталог отдельно, например `/root/vpn-shop-bot`, и создайте
`.env`:

```bash
cp .env.example .env
chmod 600 .env
```

Запуск только нового проекта:

```bash
docker compose -p vpn-shop-bot up -d --build
docker compose -p vpn-shop-bot logs -f
```

Остановить только новый бот:

```bash
docker compose -p vpn-shop-bot stop
```

Обновить после изменения файлов:

```bash
docker compose -p vpn-shop-bot up -d --build
```

Не выполняйте `docker compose down -v`, `docker system prune`,
`docker volume prune` и не подключайте этот Compose к существующей сети
`hysteria-panel_hysteria-net`.

## SQLite и резервная копия

База находится в `data/shop.db` и монтируется отдельным bind volume. После
`docker compose -p vpn-shop-bot restart` пользователи и заказы сохраняются.

Резервная копия без остановки бота:

```bash
mkdir -p backups
sqlite3 data/shop.db ".backup 'backups/shop-$(date +%Y%m%d-%H%M%S).db'"
```

Если `sqlite3` не установлен, сначала сделайте консистентную копию через
временную остановку только этого бота или установите SQLite на VPS.

## Ручная оплата

1. Пользователь выбирает тариф.
2. Бот показывает реквизиты из `.env`.
3. Пользователь отправляет фото, PDF или изображение чека.
4. Администратор получает заявку и чек.
5. Только кнопка «Подтвердить оплату» запускает выдачу в Celerity.
6. При ошибке Celerity заказ становится `failed`, повторная оплата не нужна.

Один Telegram-пользователь всегда связан с Celerity user `tg_<telegram_id>`.
Перед созданием выполняется поиск существующего пользователя. Продление
идемпотентно: целевая дата сохраняется до REST-операции, поэтому повтор после
сетевого сбоя не начисляет один заказ дважды.

## Логи

Логи находятся в `logs/vpn-shop-bot.log` и дублируются в Docker stdout. В них не
записываются BOT_TOKEN, API key и полный номер карты. Реквизиты оплаты
отправляются только пользователю через Telegram.