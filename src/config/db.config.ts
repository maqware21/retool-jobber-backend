import { drizzle } from 'drizzle-orm/node-postgres';
import { Pool } from 'pg';
import { env } from './env.config';

// DATABASE CONNECTION
export const pool = new Pool({
  connectionString: env.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: 30000,
  //   ssl: { rejectUnauthorized: false },
});
// DATABASE CONNECTION
export const db = drizzle(pool);
