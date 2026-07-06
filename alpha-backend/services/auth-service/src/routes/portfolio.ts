import { Router } from 'express';
import { PortfolioController } from '../controllers/portfolioController';
import { authenticateJWT } from '../middlewares/auth.middleware';

const router = Router();
const controller = new PortfolioController();

// All portfolio routes are protected by JWT auth
router.get('/portfolio/margins', authenticateJWT, (req, res) => controller.getMargins(req, res));
router.get('/portfolio/positions', authenticateJWT, (req, res) => controller.getPositions(req, res));
router.get('/portfolio/holdings', authenticateJWT, (req, res) => controller.getHoldings(req, res));
router.get('/portfolio/orders', authenticateJWT, (req, res) => controller.getOrders(req, res));
router.get('/portfolio/trades', authenticateJWT, (req, res) => controller.getTrades(req, res));

export default router;
