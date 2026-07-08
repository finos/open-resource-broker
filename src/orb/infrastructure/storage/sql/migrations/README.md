# SQL migrations (Alembic)

This directory holds the Alembic configuration + revision history for the
ORB SQL storage backend. Migrations only apply when the storage strategy
is configured to use `sql` — the JSON backend has no schema and never
runs these.

## Layout

```
src/orb/infrastructure/storage/sql/migrations/
  alembic.ini          # Alembic configuration (script_location = .)
  env.py               # Alembic env — loads ORM Base.metadata, reads ORB_SQL_URL
  script.py.mako       # New-revision template
  versions/            # Revision scripts, one file per migration
```

Migrations ship as part of the `orb` Python package; the CLI resolves
`alembic.ini` by importing `orb` and walking to `infrastructure/storage/sql/migrations/`
so a `pip install orb-py` is sufficient — no need to keep the repo
tree intact at the install target.

## Database URL resolution (env.py)

`env.py` resolves the database URL in this order:

1. `ORB_SQL_URL` environment variable (highest — used by the CLI).
2. `sqlalchemy.url` in `alembic.ini`.
3. Fallback `sqlite:///orb_data.db` if neither is set.

The CLI sets `ORB_SQL_URL` from `StorageConfig` in the live `ConfigurationManager`,
so what you have configured for runtime is automatically what the migration runs against.

## Running migrations

Preferred (uses ORB's CLI which wires the URL from `ConfigurationManager`):

```
orb storage migrate up
orb storage migrate down
orb storage migrate current
orb storage migrate history
```

Direct invocation (e.g. in CI or one-off scripts) — pass the URL explicitly:

```
ORB_SQL_URL=sqlite:///./orb_data.db \
  python -m alembic --config src/orb/infrastructure/storage/sql/migrations/alembic.ini upgrade head
```

## Creating a new migration

1. Edit the ORM models in `src/orb/infrastructure/storage/sql/models.py` — that
   file is the single source of truth and `env.py` imports `Base.metadata` from it.
2. Generate a revision:

   ```
   ORB_SQL_URL=sqlite:///./orb_data.db \
     python -m alembic --config src/orb/infrastructure/storage/sql/migrations/alembic.ini \
     revision --autogenerate -m "describe the change"
   ```

3. Inspect the generated script under `versions/`. Autogenerate is best-effort —
   it catches schema diffs but does not write data migrations, custom checks,
   or compound constraints. Hand-edit when needed.
4. Run `orb storage migrate up` and confirm.

## Conventions

- **Schema-only.** Migrations add / drop / alter columns and tables. They do
  not run data backfills — the ORB backend layer is multi-backend (JSON +
  SQL) and a SQL-only data migration leaves the JSON backend inconsistent.
  If a domain field becomes required, the right place to enforce it is the
  domain aggregate validator, not the migration.
- **Idempotent upgrades** are not assumed — Alembic tracks state in the
  `alembic_version` table. Do not use `IF NOT EXISTS` to mask missing
  prior revisions; track the chain properly with `down_revision`.
- **`batch_alter_table`** when altering existing columns. SQLite cannot
  rewrite columns in place; batch mode emulates the change via a table
  rebuild and works on Postgres too.
- **Downgrade paths** ship with every revision so rollback is possible.
  If a downgrade is structurally impossible (e.g. data loss), raise
  `NotImplementedError` in the `downgrade()` function with a comment
  explaining why.

## Troubleshooting

- "Target database is not up to date" — run `orb storage migrate up` first.
- "Can't locate revision identified by …" — the database's `alembic_version`
  table references a revision that was deleted from `versions/`. Recover
  by re-creating the missing file or stamping the database to a known
  revision: `orb storage migrate stamp <revision>` (run via direct alembic
  invocation if the CLI does not expose `stamp`).
- Migration succeeded but the ORM still complains at runtime — check that
  the ORM model in `models.py` matches what the migration produced; the
  ORM is the source of truth for column types and nullability.
