# postgres-pane (herdr-app mod)

Read-only side panel, next to Files and Changes, giving a basic overview of the project's PostgreSQL database: its databases (click one to switch), tables and views with exact row counts (`~` = planner estimate when a count is not allowed or takes over 2 s), and each one's columns (type, `PK`, `?` = nullable). It loads when the focused folder changes and when you press **Refresh**.

**Double-click** a table or view to open its last 100 rows in a new tab: newest first by primary key, else by physical order (roughly insertion order); views without either come unordered. Cells are cut at 300 characters, and the tab has its own **Refresh**.

`.env` is looked up in the pane's folder, then in each parent up to the git root (or `$HOME`), then one folder down. The connection comes from:

- `OCCOS_DATABASE_URL` (`postgres://user:pass@host:port/db?sslmode=…`), else
- `POSTGRES_PASSWORD`, with `POSTGRES_USER` (default `postgres`), `POSTGRES_DB`, `POSTGRES_HOST` (default `localhost`) and `POSTGRES_PORT` (default `5432`).

`pgview.py` does the work: standard-library Python (no psql or psycopg needed), with trust, cleartext, MD5 and SCRAM-SHA-256 auth and optional TLS. It runs on the pane's machine (remote spaces too), opens read-only transactions, and never prints the password.

## Install

```bash
ln -s "$PWD" ~/.config/herdr-app/mods/postgres
```

Then add a button and a stack child, like [`todo-pane`](../todo-pane/README.md) step 2–3: copy `database.svg` into the cloned `tabs-todo` mod, append `"postgres"` to its `enabled` list, add

```js
.when(enabled.includes("postgres"), el => el.child(panelButton("postgres", "DB", "mods/tabs-todo/database.svg")))
```

and in your personal layout add `{ "id": "postgres", "type": "block", "content": { "mod": "postgres" } }` to the `panel-body` stack.
