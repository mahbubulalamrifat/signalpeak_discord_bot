# SignalPeak Discord forwarder

The bot listens to source channels stored in Postgres and forwards each matching message to the destination channel. Before sending, it runs the search/replace rules stored in the database. Images and other attachments are downloaded and re-uploaded with the forwarded message.

XAMPP's MySQL is not used. Install PostgreSQL and create a database:

```sql
CREATE DATABASE signalpeak;
```

## Setup

```powershell
cd D:\xampp\htdocs\ytranker\discord_bot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Fill `.env`:

- `DISCORD_APPLICATION_ID` — Application ID from the Discord Developer Portal
- `DISCORD_PUBLIC_KEY` — Public Key from the same application page
- `DISCORD_BOT_TOKEN` — Bot token. The gateway needs this to read and forward messages
- `DATABASE_URL` — Postgres connection string

In the Developer Portal, open the Bot page and enable **Message Content Intent** and **Server Members Intent**. The bot also needs, in both servers:

- View Channel
- Send Messages
- Attach Files
- Read Message History
- View Audit Log, if you want the log to record who deleted a message or who kicked or banned a member

## Channel pairs

Edit `seed/routes.json` and replace every `REPLACE_WITH_...` value with the real numeric server and channel ids. Names are already filled in; change them to the real server and channel names.

The file is applied only when `signalpeak_discord` has no rows. After that, the bot reads pairs from the database only. Change a pair with the API or SQL. Editing the seed file later does not change an existing row.

Start the app (do not use `--reload`, or Discord will see two connections):

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Health: http://127.0.0.1:8000/health
- API docs: http://127.0.0.1:8000/docs

## Replace rules

A rule is a pair. `search_key` (or `key`) is the text or regular expression to find. `replace_value` (or `value`) is the replacement. Use `""` to remove the match.

Copy `seed/replace_rules.example.json` to `seed/replace_rules.json` before the first start if you want those rules inserted once. After the table has rows, add rules with the API:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/replace-rules -ContentType "application/json" -Body '{"key":"unwanted text","value":"","is_regex":false}'
```

Rules run in `position` order. A rule with `route_id` applies only to that channel pair. A null `route_id` applies to every pair.

## Member actions

Approve, kick, and ban are stored in `signalpeak_discord_member_actions`, then the bot performs the row.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/members/kick -ContentType "application/json" -Body '{"server_id":"SERVER_ID","user_id":"USER_ID","reason":"spam"}'
```

`approve` adds `role_id` when you send one (or `APPROVAL_ROLE_ID` from `.env`) and clears a timeout. The destination user list itself is not built yet.

## Logs

Every forward step, join, leave, delete, and member action is written to `signalpeak_discord_logs`.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/logs
```

Set `ADMIN_API_KEY` in `.env` and send header `X-API-Key` on `/api` calls when the process is reachable beyond your own machine.
