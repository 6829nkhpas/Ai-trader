import axios from 'axios';
import { kycApi } from './api-client';

export async function isOnboardingComplete(): Promise<boolean> {
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
