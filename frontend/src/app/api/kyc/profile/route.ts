// Mock KYC profile endpoint for E2E testing (ALPHA_TEST_MODE)
import { NextResponse } from 'next/server';

export async function GET() {
  if (process.env.ALPHA_TEST_MODE) {
    return NextResponse.json({
      profile: {
        id: 'test-user-001',
        kyc_status: 'VERIFIED',
        full_name: 'Alpha Tester',
        pan_number: 'XXXXX0000X',
        date_of_birth: '1990-01-01',
        risk_profile: 'AGGRESSIVE',
        trading_experience: 'EXPERT',
        created_at: '2024-01-01T00:00:00Z',
      },
    });
  }

  return NextResponse.json({ error: 'Not found' }, { status: 404 });
}
