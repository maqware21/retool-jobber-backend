# Volt Pro Backend

SaaS backend for Volt Pro — fetches job data from Jobber / HouseCall Pro, manages customer accounts, and handles Stripe subscription billing.

**Stack:** Node.js + TypeScript + Express + PostgreSQL (Drizzle ORM)

## Setup

```bash
npm install
cp .env.example .env      # fill in DATABASE_URL, JWT_SECRET, AUTH_SECRET
npm run db:generate       # generate migration from schema
npm run db:migrate        # apply migration to DB
npm run start:dev         # start dev server
```

## Scripts

| Command               | Purpose                                |
| --------------------- | -------------------------------------- |
| `npm run start:dev`   | Dev mode (tsx, auto-restart)           |
| `npm run build`       | Compile TS → `dist/`                   |
| `npm run start:prod`  | Run compiled JS (production)           |
| `npm run db:generate` | Generate migration from schema changes |
| `npm run db:migrate`  | Apply pending migrations               |

## Structure

src/
index.ts # server startup, DB check, graceful shutdown
app.ts # express app, middlewares, routes
config/ # env, db pool, logger
models/ # Drizzle schema (tables + relations)
services/ # DB queries + business logic
controllers/ # request/response handling
routes/ # route definitions
middlewares/ # error handling, auth, etc.
utils/ # helpers

Layering: `routes → controllers → services → models`. Models hold only table definitions; all queries live in services.

## Notes

- Naming: snake_case in DB, camelCase in TS (Drizzle maps automatically)
- Soft deletes via `deletedAt` column
- Multi-step writes use `db.transaction()`
- Variable-shape data (Jobber vs HouseCall Pro job payloads) stored in `jsonb` columns
- Production never runs TypeScript directly — always compiled JS in `dist/`
