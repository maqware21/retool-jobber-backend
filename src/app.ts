// src/app.ts
import express from 'express';
import morgan from 'morgan';
import helmet from 'helmet';
import cookieParser from 'cookie-parser';
import bodyParser from 'body-parser';
import compression from 'compression';
import createHttpError from 'http-errors';
import session from 'express-session';
import fileUpload from 'express-fileupload';
import cors from 'cors';
import type { Request, Response, NextFunction } from 'express';
import { createErrorResponse } from './utils/error.utils';

const app = express();

// CORS ALLOWED
app.use(cors());

// LOGGIN
app.use(morgan(process.env.NODE_ENV === 'development' ? 'dev' : 'tiny'));

// SECURITY HEADERS
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        styleSrc: ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
        scriptSrc: ["'self'"],
        imgSrc: ["'self'", 'data:', 'https:'],
        connectSrc: ["'self'"],
        fontSrc: ["'self'", 'https://fonts.gstatic.com'],
        objectSrc: ["'none'"],
        mediaSrc: ["'self'"],
        frameSrc: ["'none'"],
      },
    },
    hsts: {
      maxAge: 31536000,
      includeSubDomains: true,
      preload: true,
    },
    noSniff: true,
    xssFilter: true,
    frameguard: { action: 'deny' },
  }),
);

// BODY PARSER LIMIT
const bodyLimit = process.env.BODY_PARSER_LIMIT || '50mb';
app.use(express.json({ limit: bodyLimit }));
app.use(bodyParser.urlencoded({ limit: bodyLimit, extended: true }));

// FILE UPLOAD LIMIT
const fileUploadMaxSizeBytes =
  parseInt(process.env.FILE_UPLOAD_MAX_FILE_SIZE_MB || '20', 10) * 1024 * 1024;
const fileUploadMaxFiles = parseInt(
  process.env.FILE_UPLOAD_MAX_FILES || '100',
  10,
);

app.use(
  fileUpload({
    useTempFiles: true,
    tempFileDir: '/tmp/uploads',
    limits: {
      fileSize: fileUploadMaxSizeBytes,
      files: fileUploadMaxFiles,
    },
    abortOnLimit: true,
    safeFileNames: true,
    preserveExtension: true,
  }),
);

// COOKIE PARSER
app.use(cookieParser());
app.use(compression());

// HEALTH CHECK
app.get('/', (_req, res) => {
  res.send('Welcome to ReTool Jobber Server!');
});

// SESSION
app.use(
  session({
    secret: process.env.AUTH_SECRET as string,
    resave: false,
    saveUninitialized: false,
    name: 'voltpro.sid',
    cookie: {
      secure: process.env.NODE_ENV === 'production',
      httpOnly: true,
      sameSite: 'lax',
      maxAge: 24 * 60 * 60 * 1000,
    },
  }),
);

// ROUTES
// app.use('/api/v1', routes);

// NOT FOUND
app.use((_req: Request, _res: Response, next: NextFunction) => {
  next(createHttpError.NotFound('This route does not exist!'));
});

// ERROR HANDLER
app.use(
  (error: any, request: Request, response: Response, _next: NextFunction) => {
    if (!error) {
      error = new Error('An unknown error occurred');
    }

    if (!(error instanceof Error)) {
      const errorMessage =
        typeof error === 'string' ? error : JSON.stringify(error);
      error = new Error(errorMessage);
    }

    const isDevelopment = process.env.NODE_ENV === 'development';
    const errorResponse = createErrorResponse(error, request, isDevelopment);
    const statusCode = error.status || error.statusCode || 500;

    response.status(statusCode);
    response.json(errorResponse);
  },
);

export default app;
