// Use native global fetch available in modern Node.js

export class KiteService {
  private baseUrl = 'https://api.kite.trade';

  private getHeaders(apiKey: string, accessToken: string) {
    return {
      'Authorization': `token ${apiKey}:${accessToken}`,
      'X-Kite-Version': '3',
      'Accept': 'application/json'
    };
  }

  // 1. Margins
  async getMargins(apiKey: string, accessToken: string): Promise<any> {
    if (!apiKey || !accessToken) {
      throw new Error('MISSING_BROKER_CREDENTIALS');
    }

    try {
      const response = await fetch(`${this.baseUrl}/user/margins`, {
        method: 'GET',
        headers: this.getHeaders(apiKey, accessToken)
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Kite API Error [Margins]: ${response.status} - ${errText}`);
      }

      const res = await response.json() as any;
      return res.data;
    } catch (error: any) {
      console.error('[KiteService] Failed to fetch margins:', error.message);
      if (error.message.includes('403') || error.message.includes('401')) {
        throw new Error('UNAUTHORIZED_BROKER');
      }
      throw error;
    }
  }

  // 2. Positions
  async getPositions(apiKey: string, accessToken: string): Promise<any> {
    if (!apiKey || !accessToken) {
      throw new Error('MISSING_BROKER_CREDENTIALS');
    }

    try {
      const response = await fetch(`${this.baseUrl}/portfolio/positions`, {
        method: 'GET',
        headers: this.getHeaders(apiKey, accessToken)
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Kite API Error [Positions]: ${response.status} - ${errText}`);
      }

      const res = await response.json() as any;
      return res.data;
    } catch (error: any) {
      console.error('[KiteService] Failed to fetch positions:', error.message);
      if (error.message.includes('403') || error.message.includes('401')) {
        throw new Error('UNAUTHORIZED_BROKER');
      }
      throw error;
    }
  }

  // 3. Holdings
  async getHoldings(apiKey: string, accessToken: string): Promise<any> {
    if (!apiKey || !accessToken) {
      throw new Error('MISSING_BROKER_CREDENTIALS');
    }

    try {
      const response = await fetch(`${this.baseUrl}/portfolio/holdings`, {
        method: 'GET',
        headers: this.getHeaders(apiKey, accessToken)
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Kite API Error [Holdings]: ${response.status} - ${errText}`);
      }

      const res = await response.json() as any;
      return res.data;
    } catch (error: any) {
      console.error('[KiteService] Failed to fetch holdings:', error.message);
      if (error.message.includes('403') || error.message.includes('401')) {
        throw new Error('UNAUTHORIZED_BROKER');
      }
      throw error;
    }
  }

  // 4. Orders
  async getOrders(apiKey: string, accessToken: string): Promise<any> {
    if (!apiKey || !accessToken) {
      throw new Error('MISSING_BROKER_CREDENTIALS');
    }

    try {
      const response = await fetch(`${this.baseUrl}/orders`, {
        method: 'GET',
        headers: this.getHeaders(apiKey, accessToken)
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Kite API Error [Orders]: ${response.status} - ${errText}`);
      }

      const res = await response.json() as any;
      return res.data;
    } catch (error: any) {
      console.error('[KiteService] Failed to fetch orders:', error.message);
      if (error.message.includes('403') || error.message.includes('401')) {
        throw new Error('UNAUTHORIZED_BROKER');
      }
      throw error;
    }
  }

  // 5. Trades
  async getTrades(apiKey: string, accessToken: string): Promise<any> {
    if (!apiKey || !accessToken) {
      throw new Error('MISSING_BROKER_CREDENTIALS');
    }

    try {
      const response = await fetch(`${this.baseUrl}/trades`, {
        method: 'GET',
        headers: this.getHeaders(apiKey, accessToken)
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Kite API Error [Trades]: ${response.status} - ${errText}`);
      }

      const res = await response.json() as any;
      return res.data;
    } catch (error: any) {
      console.error('[KiteService] Failed to fetch trades:', error.message);
      if (error.message.includes('403') || error.message.includes('401')) {
        throw new Error('UNAUTHORIZED_BROKER');
      }
      throw error;
    }
  }
}
