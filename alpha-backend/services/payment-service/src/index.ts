import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import paymentRoutes from './routes/payment.routes';

dotenv.config();

const app = express();
const port = process.env.PORT || 3002;

app.use(cors());
app.use(express.json());

// Basic logging middleware
app.use((req, res, next) => {
  console.log(`[Payment Service - Layered] ${req.method} ${req.url}`);
  next();
});

// Mount routes
app.use('/api/payments', paymentRoutes);

// Base health check
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'OK', service: 'payment-service (CSR Refactored)', port });
});

app.listen(port, () => {
  console.log(`🚀 CSR Layered Payment Service running on http://localhost:${port}`);
});
