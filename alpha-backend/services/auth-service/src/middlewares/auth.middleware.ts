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
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized: Missing or malformed token' });
  }

  const token = authHeader.split(' ')[1];

  try {
    const decoded = jwt.verify(token, JWT_SECRET) as { userId: string; tier: string };
    req.user = decoded;
    next();
  } catch (error) {
    console.error('[Auth Middleware] JWT Validation failed:', error);
    return res.status(403).json({ error: 'Forbidden: Invalid or expired token' });
  }
};
