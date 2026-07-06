import { Request, Response } from 'express';

export class PhonePeController {
  // GET /api/payments/phonepe/redirect
  async handleRedirect(req: Request, res: Response): Promise<any> {
    console.log('[PhonePeController] Received callback redirect from PhonePe. Launching desktop app...');
    // Redirect back to Tauri desktop app scheme
    return res.redirect('strat://payment-success');
  }
}
