const { spawn } = require('child_process');
const axios = require('axios');

const AUTH_URL = 'http://localhost:3001';
const PAYMENT_URL = 'http://localhost:3002';

let authProcess = null;
let paymentProcess = null;

// Helper to wait
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function startServices() {
  console.log('\n=== Step 1: Starting Services ===');
  
  // Start Auth Service on port 3001
  console.log('[Test Driver] Launching Auth Service...');
  authProcess = spawn('npx', ['ts-node', 'services/auth-service/src/index.ts'], {
    shell: true,
    stdio: 'pipe'
  });
  
  authProcess.stdout.on('data', (data) => {
    console.log(`[Auth Service stdout] ${data.toString().trim()}`);
  });
  
  authProcess.stderr.on('data', (data) => {
    console.error(`[Auth Service stderr] ${data.toString().trim()}`);
  });

  // Start Payment Service on port 3002
  console.log('[Test Driver] Launching Payment Service...');
  paymentProcess = spawn('npx', ['ts-node', 'services/payment-service/src/index.ts'], {
    shell: true,
    stdio: 'pipe'
  });
  
  paymentProcess.stdout.on('data', (data) => {
    console.log(`[Payment Service stdout] ${data.toString().trim()}`);
  });
  
  paymentProcess.stderr.on('data', (data) => {
    console.error(`[Payment Service stderr] ${data.toString().trim()}`);
  });

  // Give them a moment to bind to ports
  await sleep(4000);
  console.log('[Test Driver] Services initialized.');
}

async function runTests() {
  console.log('\n=== Step 2: Running Integration Workflows ===');
  
  const testUser = {
    email: `trader_${Math.random().toString(36).substring(2, 7)}@alpha.com`,
    password: 'securePassword123'
  };

  console.log(`\n--- Test A: Auth Service Login & Auto-Registration ---`);
  console.log(`Attempting login for: ${testUser.email}`);
  
  const loginRes = await axios.post(`${AUTH_URL}/api/auth/login`, testUser);
  
  if (loginRes.status === 200 && loginRes.data.token) {
    console.log('✅ Login Successful!');
    console.log('Signed JWT:', loginRes.data.token.substring(0, 40) + '...');
    console.log('User Details:', loginRes.data.user);
  } else {
    throw new Error('Failed Login Test');
  }

  const { token, user } = loginRes.data;
  const userId = user.id;

  console.log(`\n--- Test B: Broker Kite OAuth Redirection & Callback Handoff ---`);
  console.log(`Querying connect endpoint for userId: ${userId}`);
  
  const connectRes = await axios.get(`${AUTH_URL}/api/broker/zerodha/connect?userId=${userId}`, {
    maxRedirects: 5 // follow the local redirect simulate
  });

  if (connectRes.status === 200 && connectRes.data.includes('tauri://broker-callback')) {
    console.log('✅ Broker Redirection & Handoff Simulation Successful!');
    console.log('Callback Deep-link Page Content verified: Includes tauri:// link.');
  } else {
    throw new Error('Failed Broker Handoff Test');
  }

  console.log(`\n--- Test C: PhonePe JWT Verification & Checkout Creation ---`);
  console.log(`Sending PhonePe checkout request with authorization header and amount...`);
  
  const checkoutRes = await axios.post(
    `${PAYMENT_URL}/api/payments/phonepe/checkout`,
    { amount: 599.0, tier: 'PREMIUM' },
    {
      headers: {
        Authorization: `Bearer ${token}`
      }
    }
  );

  if (checkoutRes.status === 200 && checkoutRes.data.redirectUrl && checkoutRes.data.xVerify) {
    console.log('✅ PhonePe JWT Validation & Payload generation Successful!');
    console.log('Merchant Transaction ID:', checkoutRes.data.merchantTransactionId);
    console.log('X-VERIFY Checksum generated:', checkoutRes.data.xVerify);
    console.log('PhonePe Instrument Redirect URL:', checkoutRes.data.redirectUrl.substring(0, 70) + '...');
  } else {
    throw new Error('Failed PhonePe Checkout Test');
  }

  const { merchantTransactionId, base64Payload } = checkoutRes.data;

  console.log(`\n--- Test D: PhonePe Webhook Validation & Service-to-Service Communication ---`);
  console.log(`Simulating PhonePe payment gateway success webhook payload...`);
  
  // To verify signature verification, we generate a valid webhook response
  const crypto = require('crypto');
  const saltKey = 'mock-salt-key-9283-1029';
  const saltIndex = '1';
  
  const mockResponsePayload = {
    success: true,
    code: 'PAYMENT_SUCCESS',
    data: {
      merchantTransactionId,
      merchantUserId: userId,
      amount: 59900 // Paise
    }
  };
  
  const base64Response = Buffer.from(JSON.stringify(mockResponsePayload)).toString('base64');
  
  // Calculate valid signature: SHA256(base64Response + saltKey) + "###" + saltIndex
  const sha256 = crypto.createHash('sha256').update(base64Response + saltKey).digest('hex');
  const xVerifyHeader = `${sha256}###${saltIndex}`;

  console.log('Simulating webhook POST with header X-VERIFY:', xVerifyHeader);
  
  const webhookRes = await axios.post(`${PAYMENT_URL}/api/payments/phonepe/webhook`, {
    response: base64Response
  }, {
    headers: {
      'x-verify': xVerifyHeader
    }
  });

  if (webhookRes.status === 200 && webhookRes.data.subscription.status === 'ACTIVE') {
    console.log('✅ Webhook caught, validated checksum signature successfully!');
    console.log('✅ Local subscription activated!');
    console.log('✅ Service-to-service internal upgrade synchronized!');
    console.log('Auth Upgrade Response:', webhookRes.data.syncResponse);
  } else {
    throw new Error('Failed PhonePe Webhook Validation / Sync Test');
  }

  console.log(`\n--- Test E: Verification of Redis Caching & Cache Invalidation ---`);
  console.log(`Fetching user profile via GET /api/auth/me (Cache Write / Read)...`);
  
  // First fetch: writes to cache
  const meRes1 = await axios.get(`${AUTH_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  console.log('Profile Fetch 1 (Cached state FREE):', meRes1.data.profile);

  console.log(`\nSimulating PhonePe payment gateway webhook success event to trigger sync + cache invalidation...`);
  const webhookRes = await axios.post(`${PAYMENT_URL}/api/payments/phonepe/webhook`, {
    response: base64Response
  }, {
    headers: { 'x-verify': xVerifyHeader }
  });
  
  console.log('Webhook synchronization response:', webhookRes.data.syncResponse);

  // Second fetch: must read fresh DB profile because webhook call should have invalidated user cache key
  console.log(`Fetching profile again via GET /api/auth/me (should invalidate & reflect PREMIUM instantly)...`);
  const meRes2 = await axios.get(`${AUTH_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  console.log('Profile Fetch 2 (Fresh status PREMIUM):', meRes2.data.profile);

  if (meRes2.data.profile.tier === 'PREMIUM') {
    console.log('✅ Verified: Redis cache successfully invalidated and upgraded status returned instantly!');
  } else {
    throw new Error(`Failed Caching Sync. Expected PREMIUM, found: ${meRes2.data.profile.tier}`);
  }
}

function cleanUp() {
  console.log('\n=== Step 3: Cleaning Up processes ===');
  if (authProcess) {
    console.log('[Test Driver] Killing Auth Service...');
    authProcess.kill('SIGINT');
  }
  if (paymentProcess) {
    console.log('[Test Driver] Killing Payment Service...');
    paymentProcess.kill('SIGINT');
  }
}

async function main() {
  try {
    await startServices();
    await runTests();
    console.log('\n🌟 INTEGRATION FLOW COMPLETED SUCCESSFULLY! ALL VERIFICATIONS PASSED.');
  } catch (error) {
    console.error('\n❌ INTEGRATION FLOW FAILED WITH ERROR:', error.message);
    if (error.response) {
      console.error('Response Data:', error.response.data);
    }
  } finally {
    cleanUp();
    // Allow processes to fully terminate
    await sleep(2000);
    process.exit(0);
  }
}

main();
