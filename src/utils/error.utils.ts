import type { Request } from 'express';

// ERROR MIDDLEWARE
interface ErrorWithStatus extends Error {
  status?: number;
  statusCode?: number;
}

export function createErrorResponse(
  error: ErrorWithStatus,
  request: Request,
  isDevelopment: boolean,
) {
  return {
    success: false,
    message: error.message || 'Internal server error',
    path: request.originalUrl,
    method: request.method,
    ...(isDevelopment && { stack: error.stack }),
  };
}
