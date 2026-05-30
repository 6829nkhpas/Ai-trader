import { Response } from 'express';
import { AuthenticatedRequest } from '../middlewares/auth.middleware';
import { KiteService } from '../services/kiteService';
import { prisma } from '../db';
import { redis } from '../config/redis';

const kiteService = new KiteService();
const MARGIN_CACHE_TTL = 60; // 60 seconds
const HOLDINGS_CACHE_TTL = 60; // 60 seconds

export class PortfolioController {
  // Helpers to resolve user API key and access token
  private async getBrokerCredentials(userId: string) {
    const connection = await prisma.brokerConnection.findUnique({
      where: { userId }
    });

    if (!connection || !connection.apiKey || !connection.accessToken) {
      throw new Error('NO_BROKER_CONNECTION');
    }

    return {
      apiKey: connection.apiKey,
      accessToken: connection.accessToken
    };
  }

  // GET /api/portfolio/margins
  async getMargins(req: AuthenticatedRequest, res: Response): Promise<any> {
    try {
      const userId = req.user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user context' });
      }

      const cacheKey = `portfolio:margins:${userId}`;

      // Check Redis cache first
      try {
        const cachedData = await redis.get(cacheKey);
        if (cachedData) {
          console.log(`[PortfolioController] Cache Hit! Margins returned from Redis for user: ${userId}`);
          return res.status(200).json({
            source: 'cache',
            margins: JSON.parse(cachedData)
          });
        }
      } catch (err: any) {
        console.warn(`[PortfolioController Cache] Redis get failed:`, err.message);
      }

      // Cache miss -> fetch from Kite Connect API
      const { apiKey, accessToken } = await this.getBrokerCredentials(userId);
      const margins = await kiteService.getMargins(apiKey, accessToken);

      // Save to Redis cache
      try {
        await redis.setex(cacheKey, MARGIN_CACHE_TTL, JSON.stringify(margins));
        console.log(`[PortfolioController] Margins cached in Redis for 60s (User: ${userId})`);
      } catch (err: any) {
        console.warn(`[PortfolioController Cache] Redis setex failed:`, err.message);
      }

      return res.status(200).json({
        source: 'api',
        margins
      });
    } catch (error: any) {
      console.error('[PortfolioController] getMargins failed:', error.message);
      if (error.message === 'NO_BROKER_CONNECTION') {
        return res.status(400).json({ error: 'No active broker connection. Please connect your Zerodha account.' });
      }
      if (error.message === 'UNAUTHORIZED_BROKER') {
        return res.status(403).json({ error: 'Broker session expired. Please reconnect your account.', code: 'BROKER_SESSION_EXPIRED' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/portfolio/holdings
  async getHoldings(req: AuthenticatedRequest, res: Response): Promise<any> {
    try {
      const userId = req.user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user context' });
      }

      const cacheKey = `portfolio:holdings:${userId}`;

      // Check Redis cache first
      try {
        const cachedData = await redis.get(cacheKey);
        if (cachedData) {
          console.log(`[PortfolioController] Cache Hit! Holdings returned from Redis for user: ${userId}`);
          return res.status(200).json({
            source: 'cache',
            holdings: JSON.parse(cachedData)
          });
        }
      } catch (err: any) {
        console.warn(`[PortfolioController Cache] Redis get failed:`, err.message);
      }

      // Cache miss -> fetch from Kite Connect API
      const { apiKey, accessToken } = await this.getBrokerCredentials(userId);
      const holdings = await kiteService.getHoldings(apiKey, accessToken);

      // Save to Redis cache
      try {
        await redis.setex(cacheKey, HOLDINGS_CACHE_TTL, JSON.stringify(holdings));
        console.log(`[PortfolioController] Holdings cached in Redis for 60s (User: ${userId})`);
      } catch (err: any) {
        console.warn(`[PortfolioController Cache] Redis setex failed:`, err.message);
      }

      return res.status(200).json({
        source: 'api',
        holdings
      });
    } catch (error: any) {
      console.error('[PortfolioController] getHoldings failed:', error.message);
      if (error.message === 'NO_BROKER_CONNECTION') {
        return res.status(400).json({ error: 'No active broker connection. Please connect your Zerodha account.' });
      }
      if (error.message === 'UNAUTHORIZED_BROKER') {
        return res.status(403).json({ error: 'Broker session expired. Please reconnect your account.', code: 'BROKER_SESSION_EXPIRED' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/portfolio/positions
  async getPositions(req: AuthenticatedRequest, res: Response): Promise<any> {
    try {
      const userId = req.user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user context' });
      }

      // No caching allowed for positions (ensure real-time P&L accuracy)
      const { apiKey, accessToken } = await this.getBrokerCredentials(userId);
      const positions = await kiteService.getPositions(apiKey, accessToken);

      return res.status(200).json({
        source: 'api',
        positions
      });
    } catch (error: any) {
      console.error('[PortfolioController] getPositions failed:', error.message);
      if (error.message === 'NO_BROKER_CONNECTION') {
        return res.status(400).json({ error: 'No active broker connection. Please connect your Zerodha account.' });
      }
      if (error.message === 'UNAUTHORIZED_BROKER') {
        return res.status(403).json({ error: 'Broker session expired. Please reconnect your account.', code: 'BROKER_SESSION_EXPIRED' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/portfolio/orders
  async getOrders(req: AuthenticatedRequest, res: Response): Promise<any> {
    try {
      const userId = req.user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user context' });
      }

      // No caching allowed for orders
      const { apiKey, accessToken } = await this.getBrokerCredentials(userId);
      const orders = await kiteService.getOrders(apiKey, accessToken);

      return res.status(200).json({
        source: 'api',
        orders
      });
    } catch (error: any) {
      console.error('[PortfolioController] getOrders failed:', error.message);
      if (error.message === 'NO_BROKER_CONNECTION') {
        return res.status(400).json({ error: 'No active broker connection. Please connect your Zerodha account.' });
      }
      if (error.message === 'UNAUTHORIZED_BROKER') {
        return res.status(403).json({ error: 'Broker session expired. Please reconnect your account.', code: 'BROKER_SESSION_EXPIRED' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/portfolio/trades
  async getTrades(req: AuthenticatedRequest, res: Response): Promise<any> {
    try {
      const userId = req.user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user context' });
      }

      // No caching allowed for trades
      const { apiKey, accessToken } = await this.getBrokerCredentials(userId);
      const trades = await kiteService.getTrades(apiKey, accessToken);

      return res.status(200).json({
        source: 'api',
        trades
      });
    } catch (error: any) {
      console.error('[PortfolioController] getTrades failed:', error.message);
      if (error.message === 'NO_BROKER_CONNECTION') {
        return res.status(400).json({ error: 'No active broker connection. Please connect your Zerodha account.' });
      }
      if (error.message === 'UNAUTHORIZED_BROKER') {
        return res.status(403).json({ error: 'Broker session expired. Please reconnect your account.', code: 'BROKER_SESSION_EXPIRED' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }
}
