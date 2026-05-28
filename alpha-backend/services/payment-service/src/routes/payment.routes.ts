import { Router } from 'express';
import { PaymentController } from '../controllers/payment.controller';
import { authenticateJWT } from '../middlewares/auth.middleware';

const router = Router();
const controller = new PaymentController();

// Strictly route maps - 0 database operations
router.post('/phonepe/checkout', authenticateJWT, (req, res) => controller.phonepeCheckout(req, res));
router.post('/phonepe/webhook', (req, res) => controller.phonepeWebhook(req, res));

export default router;
