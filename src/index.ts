// src/index.ts
import './config/env.config';
import express from 'express';
import logger from './config/logger.config';
import { pool } from './config/db.config';

// APP
import app from './app';

const PORT = Number(process.env.PORT) || 8000;

let server: ReturnType<typeof app.listen>;

// DATABASE CONNECTION
const connectToDatabase = async (): Promise<void> => {
  try {
    await pool.query('SELECT 1');
    logger.info('Connected with PostgreSQL');
  } catch (error) {
    logger.error(`Connection with DB Failed: ${error}`);
    throw error;
  }
};

// APPLICATION INITIALIZATION
const initializeApp = async (): Promise<ReturnType<typeof app.listen>> => {
  try {
    await connectToDatabase();

    const runningServer = app.listen(PORT, () => {
      logger.info(`Volt Pro Backend is Listening on Port ${PORT}`);
      logger.info('Application initialized successfully');
    });

    return runningServer;
  } catch (error: any) {
    logger.error(`Application initialization failed: ${error.message}`);
    process.exit(1);
  }
};

// APPLICATION INITIALIZATION
initializeApp()
  .then((initializedServer) => {
    server = initializedServer;
  })
  .catch((error) => {
    logger.error(`Failed to start application: ${error.message}`);
    process.exit(1);
  });

// EXCEPTION HANDLING
const exitHandler = (): void => {
  if (server) {
    logger.info('Server Gracefully Shutting Down...');
    process.exit(1);
  } else {
    process.exit(1);
  }
};

const unExpectedErrorHandler = (error: Error): void => {
  logger.error(error);
  exitHandler();
};

process.on('uncaughtException', unExpectedErrorHandler);
process.on('unhandledRejection', unExpectedErrorHandler);

process.on('SIGTERM', async () => {
  if (server) {
    await pool.end();
    logger.info('Server closed');
    process.exit(1);
  }
});

process.on('SIGINT', async () => {
  logger.info('SIGINT received. Server closing...');
  await pool.end();
  exitHandler();
});
