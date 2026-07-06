import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';

const JWT_SECRET = process.env.JWT_SECRET || 'alpha-jwt-secret-key-39281!@';

export interface AuthenticatedRequest extends Request {
  user?: {
    userId: string;
    tier: string;
  };
}

export const authenticateJWT = (req: AuthenticatedRequest, res: Response, next: NextFunction): any => {
  // Bypass JWT authentication in development mode when MOCK_BROKER is enabled
  if (process.env.MOCK_BROKER === 'true') {
    req.user = {
      userId: '1014418d-08a1-4372-800b-f4a21d2cbfe3', // Match user profile in database
      tier: 'PREMIUM'
    };
    return next();
  }

  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: Missing or malformed token', code: 'NO_TOKEN' });
  }

  const token = authHeader.split(' ')[1];

  try {
    const decoded = jwt.verify(token, JWT_SECRET) as { userId: string; tier: string };
    req.user = decoded;
    next();
  } catch (error: any) {
    // Differentiate expiration vs malformed/invalid signature so the client
    // can react correctly (re-login on 401 vs treat 403 as a permission issue).
    if (error?.name === 'TokenExpiredError') {
      console.warn('[Auth Middleware] JWT expired at', error.expiredAt);
      return res.status(401).json({ error: 'Unauthorized: Session expired', code: 'TOKEN_EXPIRED' });
    }
    console.error('[Auth Middleware] JWT validation failed:', error?.message || error);
    return res.status(401).json({ error: 'Unauthorized: Invalid token', code: 'INVALID_TOKEN' });
  }
};
