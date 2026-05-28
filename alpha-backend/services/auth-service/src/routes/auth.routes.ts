import { Router } from 'express';
import { AuthController } from '../controllers/auth.controller';
import { authenticateJWT } from '../middlewares/auth.middleware';

const router = Router();
const controller = new AuthController();

// Route definitions only — zero database/business logic here
router.post('/auth/login', (req, res) => controller.login(req, res));
router.post('/auth/signup', (req, res) => controller.signup(req, res));
router.get('/broker/zerodha/connect', (req, res) => controller.connectBroker(req, res));
router.get('/broker/zerodha/callback', (req, res) => controller.callbackBroker(req, res));
router.post('/internal/upgrade-tier', (req, res) => controller.upgradeTier(req, res));
router.get('/auth/me', authenticateJWT, (req, res) => controller.getMe(req, res));
router.post('/auth/subscription/tier', authenticateJWT, (req, res) => controller.updateSubscriptionTier(req, res));

export default router;
