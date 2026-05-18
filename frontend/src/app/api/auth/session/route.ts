// Mock auth session endpoint for E2E testing (ALPHA_TEST_MODE)
import { NextResponse } from 'next/server';

export async function GET() {
  // Check for test mode (any truthy value)
  if (process.env.ALPHA_TEST_MODE) {
    return NextResponse.json({
      user: {
        id: 'test-user-001',
        email: 'alpha@test.local',
        displayName: 'Alpha Tester',
        avatarUrl: null,
        mfaEnabled: false,
        kycStatus: 'VERIFIED',
        subscriptionStatus: 'ACTIVE',
        createdAt: '2024-01-01T00:00:00Z',
      },
    });
  }

  // In production, this route won't be hit (rewrites proxy to auth service)
  return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
}
