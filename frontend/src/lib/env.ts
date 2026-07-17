const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
const DASHBOARD_URL = process.env.NEXT_PUBLIC_DASHBOARD_URL;

if (!API_BASE_URL) {
  throw new Error(
    'Missing NEXT_PUBLIC_API_BASE_URL. Set it in frontend/.env.local (see .env.example).'
  );
}
if (!DASHBOARD_URL) {
  throw new Error(
    'Missing NEXT_PUBLIC_DASHBOARD_URL. Set it in frontend/.env.local (see .env.example).'
  );
}

export { API_BASE_URL, DASHBOARD_URL };

export const API_V1_PREFIX = '/api/v1';
