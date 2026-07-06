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
    if (process.env.MOCK_BROKER === 'true') {
      return {
        equity: {
          enabled: true,
          net: 1000000.0,
          available: {
            adhoc_margin: 0,
            cash: 1000000.0,
            collateral: 0,
            intraday_payback: 0,
            live_balance: 1000000.0,
            opening_balance: 1000000.0
          }
        },
        commodity: {
          enabled: false,
          net: 0,
          available: {
            adhoc_margin: 0,
            cash: 0,
            collateral: 0,
            intraday_payback: 0,
            live_balance: 0,
            opening_balance: 0
          }
        }
      };
    }

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
    if (process.env.MOCK_BROKER === 'true') {
      return {
        net: [
          {
            tradingsymbol: "RELIANCE",
            exchange: "NSE",
            instrument_token: 738561,
            product: "CNC",
            quantity: 10,
            overnight_quantity: 10,
            multiplier: 1,
            average_price: 2450.5,
            close_price: 2465.0,
            last_price: 2468.2,
            value: -24505.0,
            pnl: 177.0,
            realised: 0,
            unrealised: 177.0,
            buy_quantity: 10,
            buy_price: 2450.5,
            buy_value: 24505.0,
            sell_quantity: 0,
            sell_price: 0,
            sell_value: 0
          }
        ],
        day: []
      };
    }

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
    if (process.env.MOCK_BROKER === 'true') {
      return [
        {
          tradingsymbol: "RELIANCE",
          exchange: "NSE",
          instrument_token: 738561,
          isin: "INE002A01018",
          product: "CNC",
          price: 2450.5,
          quantity: 10,
          t1_quantity: 0,
          realised_quantity: 10,
          authorised_quantity: 10,
          collateral_quantity: 0,
          collateral_type: "",
          average_price: 2450.5,
          last_price: 2468.2,
          pnl: 177.0,
          close_price: 2465.0
        }
      ];
    }

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
    if (process.env.MOCK_BROKER === 'true') {
      return [
        {
          order_id: "230601000000001",
          parent_order_id: null,
          exchange_order_id: "1100000000000001",
          placed_by: "DEV",
          variety: "regular",
          status: "COMPLETE",
          status_message: null,
          status_message_raw: null,
          order_class: null,
          tradingsymbol: "RELIANCE",
          exchange: "NSE",
          instrument_token: 738561,
          transaction_type: "BUY",
          order_type: "LIMIT",
          quantity: 10,
          filled_quantity: 10,
          pending_quantity: 0,
          cancelled_quantity: 0,
          disclosed_quantity: 0,
          price: 2450.5,
          trigger_price: 0,
          validity: "DAY",
          validity_ttl: 0,
          product: "CNC",
          source: "web",
          tag: null,
          guid: "dev-guid",
          order_timestamp: "2026-06-01 06:44:31",
          exchange_timestamp: "2026-06-01 06:44:31",
          exchange_update_timestamp: "2026-06-01 06:44:31",
          status_info: {}
        }
      ];
    }

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
    if (process.env.MOCK_BROKER === 'true') {
      return [
        {
          trade_id: "T000001",
          order_id: "230601000000001",
          exchange_order_id: "1100000000000001",
          tradingsymbol: "RELIANCE",
          exchange: "NSE",
          instrument_token: 738561,
          transaction_type: "BUY",
          product: "CNC",
          average_price: 2450.5,
          quantity: 10,
          fill_timestamp: "2026-06-01 06:44:31"
        }
      ];
    }

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
