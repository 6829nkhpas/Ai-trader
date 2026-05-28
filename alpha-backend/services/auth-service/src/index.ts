import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import authRoutes from './routes/auth.routes';
import portfolioRoutes from './routes/portfolio';

dotenv.config();

const app = express();
const port = process.env.PORT || 3001;

app.use(cors());
app.use(express.json());

// Boot logging middleware
app.use((req, res, next) => {
  console.log(`[Auth Service - Layered] ${req.method} ${req.url}`);
  next();
});

// Register routers
app.use('/api', authRoutes);
app.use('/api', portfolioRoutes);

// Base status route
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'OK', service: 'auth-service (CSR Refactored)', port });
});

app.listen(port, () => {
  console.log(`🚀 CSR Layered Auth Service running on http://localhost:${port}`);
});
