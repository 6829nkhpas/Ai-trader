// Mock Kite instruments search endpoint for E2E testing (ALPHA_TEST_MODE)
import { NextResponse } from 'next/server';

const MOCK_INSTRUMENTS = [
  {
    instrument_token: 738561,
    exchange_token: 2885,
    tradingsymbol: "RELIANCE",
    name: "RELIANCE INDUSTRIES LTD",
    last_price: 2468.0,
    tick_size: 0.05,
    lot_size: 1,
    instrument_type: "EQ",
    segment: "NSE",
    exchange: "NSE",
  },
  {
    instrument_token: 341249,
    exchange_token: 1333,
    tradingsymbol: "TCS",
    name: "TATA CONSULTANCY SERVICES LTD",
    last_price: 3450.0,
    tick_size: 0.05,
    lot_size: 1,
    instrument_type: "EQ",
    segment: "NSE",
    exchange: "NSE",
  },
  {
    instrument_token: 25601,
    exchange_token: 100,
    tradingsymbol: "NIFTY 50",
    name: "NIFTY 50 INDEX",
    last_price: 22000.0,
    tick_size: 0.05,
    lot_size: 50,
    instrument_type: "INDEX",
    segment: "INDICES",
    exchange: "NSE",
  },
];

export async function GET(request: Request) {
  if (!process.env.ALPHA_TEST_MODE) {
    return NextResponse.json({ error: 'Service unavailable' }, { status: 503 });
  }

  const { searchParams } = new URL(request.url);
  const query = (searchParams.get('q') || '').toUpperCase();

  if (!query) {
    return NextResponse.json({ results: [] });
  }

  const results = MOCK_INSTRUMENTS.filter((inst) =>
    inst.tradingsymbol.toUpperCase().includes(query) ||
    inst.name.toUpperCase().includes(query)
  );

  return NextResponse.json({ results });
}
