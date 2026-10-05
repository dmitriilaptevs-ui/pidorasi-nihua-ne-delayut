# Инфраструктура: запуск стека на ноутбуке

Развёртывание по [ADR-0002](../../docs/adr/0002-hosting-topology.md): стек работает на этом ноутбуке, публичный HTTPS-вход — исходящий туннель, исходящие вызовы к OpenRouter идут через существующий туннель Happ → DE.

## Состав

| Сервис | Образ | Порт на хосте | Назначение |
| --- | --- | --- | --- |
| `db` | postgres:16-alpine | — (только внутри сети) | PostgreSQL, том `db-data` |
| `redis` | redis:7-alpine | — | лимиты/кеш, без финансовой истины |
| `api` | сборка `apps/api` | `127.0.0.1:8080` | FastAPI, `/healthz`, `/readyz` |
| `web` | сборка `apps/web` | `127.0.0.1:3001` | Next.js «Холст» |

Наружу не публикуется ни один порт: публичный доступ даёт только туннель. Лимиты памяти: db 512m, redis 192m, api 512m, web 640m — стек не должен вытеснять рабочий стол.

## Предварительно (один раз)

1. Доступ к Docker (интерактивно, пароль вводит владелец):
   `sudo usermod -aG docker dima`
   Проверка в новой команде: `sg docker -c 'docker ps'`.
2. `cp infra/.env.example infra/.env` и заполнить `POSTGRES_PASSWORD` и `PUBLIC_ORIGIN` (режим `0600`).
3. Секреты OAuth/OpenRouter — в существующем `apps/web/.env.local` (`0600`), он же читается контейнером `web`.

## Запуск

```bash
cd infra
cp .env.example .env   # один раз, затем отредактировать
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8080/readyz
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/api/status
```

Остановка без удаления данных: `docker compose down`. Полный сброс данных: `docker compose down -v` (уничтожает том `db-data`).

## Туннель

| Вариант | Команда | Статус (2026-10-05) |
| --- | --- | --- |
| localhost.run | `ssh -R 80:localhost:3001 nokey@localhost.run` | **Используется**: текущий адрес `https://8708c5f8153fe4.lhr.life`, владелец подтвердил открытие из РФ без VPN |
| Tailscale Funnel | включить на <https://login.tailscale.com/f/funnel>, затем `tailscale funnel --bg 3001` | Не включён в tailnet — CLI отвечает «Funnel is not enabled»; ожидает повторного включения владельцем |
| Cloudflare Tunnel | `cloudflared tunnel --url http://127.0.0.1:3001` | Не работает с этого хоста: edge недоступен (TCP handshake EOF и QUIC timeout) |

Поддомен localhost.run меняется при переподключении. После смены туннеля обновить `PUBLIC_ORIGIN` в `infra/.env` и перезапустить `web`:
`docker compose up -d web`.

## Выполненные проверки (2026-10-05)

| Проверка | Результат |
| --- | --- |
| `docker compose up -d --build` | db/redis/api/web запущены, api `/readyz` → 200 `{database:true, redis:true}` |
| Внешний адрес через туннель | `/` → 307 на `/canvas`, `/api/status` → 200, POST с Origin туннеля доходит до проверки сессии (401, не 403) |
| Перезапуск контейнеров | `docker compose restart` → все четыре healthy, эндпоинты отвечают |
| Backup → снос → restore | маркерная строка восстановлена из дампа (`drill-1 \| before-backup`) |
| Изоляция портов | опубликованы только `127.0.0.1:3001` и `127.0.0.1:8080`; db/redis наружу не выставлены |
| Влияние на рабочий стол | loopback-прокси `127.0.0.1:80`, `tailscaled` и `docker` не затронуты, хост не перезагружался |

## Backup и restore

```bash
infra/scripts/backup.sh                      # ~/rubai-backups/rubai-<ts>.dump + sha256, хранит 10 последних
CONFIRM_RESTORE=yes infra/scripts/restore.sh ~/rubai-backups/rubai-<ts>.dump
```

Учебное восстановление: сделать бэкап, `docker compose exec -T db psql -U rubai -d rubai -c 'SELECT count(*) FROM information_schema.tables'`, очистить том, поднять стек, восстановить дамп, повторить запрос и сравнить.

## Диагностика

- `docker compose logs -f api web` — логи сервисов; секреты в логи не попадают.
- `/readyz` отдаёт 503, пока PostgreSQL и Redis не готовы; `web` стартует после `api`.
- Если туннель отдаёт ошибку, проверить локальные адреса выше: туннель сам по себе ничего не исправляет.
- Kill switch Happ блокирует TCP вне туннеля: локальные проверки делать на loopback, не на Wi-Fi-адресе.
