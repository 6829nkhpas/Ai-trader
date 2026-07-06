import crypto from 'crypto';
import axios from 'axios';
import { SubscriptionRepository } from '../repositories/subscription.repository';

const subscriptionRepository = new SubscriptionRepository();

const MERCHANT_ID = process.env.PHONEPE_MERCHANT_ID || 'MERCHANT_MOCK_123';
const SALT_KEY = process.env.PHONEPE_SALT_KEY || 'mock-salt-key-9283-1029';
const SALT_INDEX = process.env.PHONEPE_SALT_INDEX || '1';
const AUTH_SERVICE_URL = process.env.AUTH_SERVICE_URL || 'http://localhost:3001';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || 'alpha-internal-super-secret-key-29831!';

export class PaymentService {
  // 1. Core Checkout Creation Logic
  async createPhonePeCheckout(userId: string, amount: number, tier: string) {
    const merchantTransactionId = `MT_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
    
    // Construct Paise payload
    const phonepePayload = {
      merchantId: MERCHANT_ID,
      merchantTransactionId,
      merchantUserId: userId,
      amount: Math.round(amount * 100),
      redirectUrl: `http://localhost:3002/api/payments/phonepe/redirect?transactionId=${merchantTransactionId}`,
      callbackUrl: `http://localhost:3002/api/payments/phonepe/webhook`,
      paymentInstrument: {
        type: 'PAY_PAGE'
      }
    };

    const base64Payload = Buffer.from(JSON.stringify(phonepePayload)).toString('base64');

    // SHA256(base64Payload + "/pg/v1/pay" + SALT_KEY) + "###" + SALT_INDEX
    const stringToHash = base64Payload + '/pg/v1/pay' + SALT_KEY;
    const sha256 = crypto.createHash('sha256').update(stringToHash).digest('hex');
    const xVerify = `${sha256}###${SALT_INDEX}`;

    const mockRedirectUrl = `https://merchants.phonepe.mock/pay?transactionId=${merchantTransactionId}&amount=${amount}&payload=${base64Payload}&xVerify=${xVerify}`;

    return {
      merchantTransactionId,
      base64Payload,
      xVerify,
      redirectUrl: mockRedirectUrl,
      userId,
      tier
    };
  }

  // 2. Core Webhook Evaluation Logic
  async processPhonePeWebhook(response: string, xVerifyHeader: string) {
    // Checksum verification: SHA256(response + SALT_KEY) + "###" + SALT_INDEX
    const stringToHash = response + SALT_KEY;
    const calculatedSha256 = crypto.createHash('sha256').update(stringToHash).digest('hex');
    const expectedXVerify = `${calculatedSha256}###${SALT_INDEX}`;

    if (xVerifyHeader !== expectedXVerify) {
      throw new Error('Checksum signature validation failed');
    }

    const decoded = JSON.parse(Buffer.from(response, 'base64').toString('utf-8'));
    const { success, code, data } = decoded;

    if (code === 'PAYMENT_SUCCESS' || success === true) {
      const userId = data.merchantUserId;
      const tier = 'PREMIUM';
      
      return await this.executeTierUpgradeSync(userId, tier);
    } else {
      console.warn(`[PaymentService] Webhook transaction failed. Code: ${code}`);
      return { status: 'failed', code };
    }
  }

  // Bypassed trigger fallback for testing
  async forceUpgradeSync(userId: string, tier: string) {
    return await this.executeTierUpgradeSync(userId, tier);
  }

  // Helper doing DB upsert + inter-service HTTP POST
  private async executeTierUpgradeSync(userId: string, tier: string) {
    const currentPeriodEnd = new Date();
    currentPeriodEnd.setDate(currentPeriodEnd.getDate() + 30);

    const subscription = await subscriptionRepository.upsertSubscription(userId, {
      status: 'ACTIVE',
      currentPeriodEnd,
      stripeCustomerId: 'phonepe_merchant_cust'
    });

    console.log(`[PaymentService] Local database subscription activated for user ${userId}.`);

    // Synchronize over network using secure Axios inter-service call
    console.log(`[PaymentService] Synchronizing user upgrade to auth service over network...`);
    const response = await axios.post(
      `${AUTH_SERVICE_URL}/api/internal/upgrade-tier`,
      { userId, tier },
      {
        headers: {
          'Content-Type': 'application/json',
          'x-internal-key': INTERNAL_API_KEY
        }
      }
    );

    return {
      subscription,
      syncResponse: response.data
    };
  }
}
