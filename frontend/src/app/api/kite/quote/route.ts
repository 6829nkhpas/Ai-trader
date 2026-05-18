// Mock Kite quote endpoint for E2E testing (ALPHA_TEST_MODE)
import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  if (process.env.ALPHA_TEST_MODE) {
    const { searchParams } = new URL(request.url);
    const instrument = searchParams.get('i') || 'NSE:RELIANCE';
    const symbol = instrument.split(':')[1] || 'RELIANCE';

    return NextResponse.json({
      quotes: [
        {
          symbol,
          last_price: 2468.0,
          open: 2450.0,
          high: 2475.0,
          low: 2440.0,
          close: 2445.0,
          change: 0.94,
          net_change: 23.0,
          volume: 125000,
        },
      ],
    });
  }

  return NextResponse.json({ error: 'Service unavailable' }, { status: 503 });
}
