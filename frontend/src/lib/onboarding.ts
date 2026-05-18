import axios from 'axios';
import { kycApi } from './api-client';

export async function isOnboardingComplete(): Promise<boolean> {
  // In E2E test mode, bypass the KYC check entirely
  if (typeof window !== 'undefined' && (window as unknown as Record<string, unknown>).__ALPHA_TEST_MODE__) {
    return true;
  }

  try {
    const res = await kycApi.getProfile();
    const profile = (res.data as { profile?: { kyc_status?: string } }).profile;
    if (!profile) {
      return false;
    }
    if (!profile.kyc_status || profile.kyc_status === 'PENDING') {
      return false;
    }
    return true;
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) {
      return false;
    }
    throw err;
  }
}

export async function resolvePostAuthDestination(): Promise<string> {
  const complete = await isOnboardingComplete();
  return complete ? '/dashboard' : '/auth/onboarding';
}
