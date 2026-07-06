import { Request, Response } from 'express';
import { AuthService } from '../services/auth.service';

const authService = new AuthService();

export class AuthController {
  // POST /api/auth/login
  async login(req: Request, res: Response): Promise<any> {
    try {
      const { email, password } = req.body;

      if (!email || !password) {
        return res.status(400).json({ error: 'Email and password are required' });
      }

      const result = await authService.authenticateUser(email, password);
      return res.status(200).json({
        message: 'Login successful',
        ...result
      });
    } catch (error: any) {
      console.error('[AuthController] login failed:', error.message);
      if (error.message === 'Invalid credentials') {
        return res.status(401).json({ error: 'Invalid credentials' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // POST /api/auth/signup
  async signup(req: Request, res: Response): Promise<any> {
    try {
      const { email, password, tier } = req.body;

      if (!email || !password) {
        return res.status(400).json({ error: 'Email and password are required' });
      }

      const result = await authService.registerUser(email, password, tier || 'FREE');
      return res.status(201).json({
        message: 'Registration successful',
        ...result
      });
    } catch (error: any) {
      console.error('[AuthController] signup failed:', error.message);
      if (error.message === 'User already exists') {
        return res.status(409).json({ error: 'User with this email already exists' });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/broker/zerodha/connect
  async connectBroker(req: Request, res: Response): Promise<any> {
    try {
      const { userId } = req.query;
      const result = await authService.getBrokerConnectUrl(userId as string);
      
      // If we had to resolve to a query-filled redirect
      if (result.userId !== userId) {
        return res.redirect(`/api/broker/zerodha/connect?userId=${result.userId}`);
      }

      return res.redirect(result.callbackUrl);
    } catch (error: any) {
      console.error('[AuthController] connectBroker failed:', error.message);
      return res.status(400).json({ error: error.message });
    }
  }

  // GET /api/broker/zerodha/callback
  async callbackBroker(req: Request, res: Response): Promise<any> {
    try {
      const { request_token, state } = req.query;

      if (!request_token) {
        return res.status(400).json({ error: 'Missing request_token' });
      }

      const result = await authService.saveBrokerAccessToken(state as string || undefined, request_token as string);

      // Return premium Tauri deep link callback html redirect page
      res.setHeader('Content-Type', 'text/html');
      return res.status(200).send(`
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>Broker Connection Authorized</title>
          <style>
            body {
              background-color: #0b0f19;
              color: #f3f4f6;
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
              display: flex;
              align-items: center;
              justify-content: center;
              height: 100vh;
              margin: 0;
              overflow: hidden;
            }
            body::before {
              content: '';
              position: absolute;
              inset: 0;
              background-image: radial-gradient(#10b981 1px, transparent 1px);
              background-size: 32px 32px;
              opacity: 0.04;
              z-index: -1;
            }
            .card {
              background: rgba(11, 15, 25, 0.65);
              backdrop-filter: blur(20px);
              -webkit-backdrop-filter: blur(20px);
              border: 1px solid rgba(255, 255, 255, 0.08);
              border-radius: 24px;
              padding: 40px;
              text-align: center;
              width: 100%;
              max-width: 360px;
              box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
              animation: slideUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
              position: relative;
            }
            .card::before {
              content: '';
              position: absolute;
              top: -12%;
              left: -12%;
              width: 160px;
              height: 160px;
              border-radius: 50%;
              background: rgba(16, 185, 129, 0.05);
              filter: blur(40px);
              z-index: -1;
            }
            @keyframes slideUp {
              from { opacity: 0; transform: translateY(30px); }
              to { opacity: 1; transform: translateY(0); }
            }
            .logo-container {
              width: 64px;
              height: 64px;
              background: rgba(16, 185, 129, 0.08);
              border: 1px solid rgba(16, 185, 129, 0.2);
              border-radius: 20px;
              display: flex;
              align-items: center;
              justify-content: center;
              margin: 0 auto 20px auto;
              color: #10b981;
            }
            .logo {
              width: 30px;
              height: 30px;
            }
            .spinner {
              border: 3px solid rgba(16, 185, 129, 0.15);
              width: 42px;
              height: 42px;
              border-radius: 50%;
              border-left-color: #10b981;
              animation: spin 1s linear infinite;
              margin: 20px auto 10px auto;
            }
            @keyframes spin {
              0% { transform: rotate(0deg); }
              100% { transform: rotate(360deg); }
            }
            .subtitle {
              color: #10b981;
              font-size: 10px;
              font-weight: 700;
              text-transform: uppercase;
              letter-spacing: 0.12em;
              margin-bottom: 6px;
            }
            h1 {
              font-size: 20px;
              font-weight: 800;
              margin: 0 0 10px 0;
              color: #ffffff;
              letter-spacing: -0.02em;
            }
            p {
              color: #9ca3af;
              font-size: 13px;
              line-height: 1.5;
              margin: 8px 0;
            }
            .btn {
              background: #10b981;
              color: white;
              border: none;
              padding: 12px 28px;
              border-radius: 12px;
              cursor: pointer;
              font-weight: 700;
              font-size: 11px;
              letter-spacing: 0.05em;
              text-transform: uppercase;
              margin-top: 24px;
              text-decoration: none;
              display: inline-block;
              transition: all 0.2s ease;
              box-shadow: 0 4px 14px rgba(16, 185, 129, 0.25);
            }
            .btn:hover {
              background: #059669;
              transform: translateY(-1px);
              box-shadow: 0 6px 20px rgba(16, 185, 129, 0.35);
            }
            .btn:active {
              transform: translateY(0);
            }
          </style>
        </head>
        <body>
          <div class="card">
            <div class="logo-container">
              <svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 21h18" />
                <path d="M3 10h18" />
                <path d="m5 6 7-3 7 3" />
                <path d="M4 10v11" />
                <path d="M20 10v11" />
                <path d="M8 14v3" />
                <path d="M12 14v3" />
                <path d="M16 14v3" />
              </svg>
            </div>
            <div class="subtitle">Connection Successful</div>
            <h1>Access Authorized</h1>
            <p>We've successfully authenticated and linked your Zerodha Kite broker connection.</p>
            
            <div class="spinner"></div>
            <p style="font-size: 11px; color: #6b7280; margin-top: 15px;">
              Redirecting you back to your Alpha Suite Terminal...
            </p>
            
            <a class="btn" href="strat://broker-callback?status=success&userId=${result.userId}&access_token=${result.accessToken}">Return to Tauri App</a>
          </div>
          <script>
            setTimeout(() => {
              window.location.href = "strat://broker-callback?status=success&userId=${result.userId}&access_token=${result.accessToken}";
            }, 2000);
          </script>
        </body>
        </html>
      `);
    } catch (error: any) {
      console.error('[AuthController] callbackBroker failed:', error.message);
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // POST /api/internal/upgrade-tier
  async upgradeTier(req: Request, res: Response): Promise<any> {
    try {
      const internalKey = req.headers['x-internal-key'];
      const expectedKey = process.env.INTERNAL_API_KEY || 'alpha-internal-super-secret-key-29831!';

      if (!internalKey || internalKey !== expectedKey) {
        console.warn(`[AuthController] Unauthorized internal upgrade attempt.`);
        return res.status(403).json({ error: 'Forbidden: Invalid internal API key' });
      }

      const { userId, tier } = req.body;

      if (!userId || !tier) {
        return res.status(400).json({ error: 'userId and tier are required' });
      }

      const updatedUser = await authService.upgradeUserTier(userId, tier);
      return res.status(200).json({
        message: 'User tier upgraded successfully',
        user: updatedUser
      });
    } catch (error: any) {
      console.error('[AuthController] upgradeTier failed:', error.message);
      if (error.message === 'User profile not found') {
        return res.status(404).json({ error: error.message });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // GET /api/auth/me
  // Authenticated endpoint returning user profile details (uses Redis Caching)
  async getMe(req: Request, res: Response): Promise<any> {
    try {
      const userId = (req as any).user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user credentials context' });
      }

      const profile = await authService.getUserProfile(userId);
      return res.status(200).json({
        profile
      });
    } catch (error: any) {
      console.error('[AuthController] getMe failed:', error.message);
      if (error.message === 'User not found') {
        return res.status(404).json({ error: error.message });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }

  // POST /api/auth/subscription/tier (Requires JWT)
  async updateSubscriptionTier(req: Request, res: Response): Promise<any> {
    try {
      const userId = (req as any).user?.userId;
      if (!userId) {
        return res.status(401).json({ error: 'Unauthorized: Missing user credentials context' });
      }

      const { tier } = req.body;
      if (!tier || (tier !== 'FREE' && tier !== 'PRO')) {
        return res.status(400).json({ error: 'Invalid tier specified. Supported: FREE, PRO' });
      }

      const updatedUser = await authService.upgradeUserTier(userId, tier);
      return res.status(200).json({
        message: `Subscription successfully updated to ${tier}`,
        user: updatedUser
      });
    } catch (error: any) {
      console.error('[AuthController] updateSubscriptionTier failed:', error.message);
      if (error.message === 'User profile not found') {
        return res.status(404).json({ error: error.message });
      }
      return res.status(500).json({ error: 'Internal server error', details: error.message });
    }
  }
}
