// integration_test.js — End-to-end registration verification
// Run: node src/__tests__/integration_test.js

import { config } from '../config.js';
import { getPool, closePool } from '../db.js';
import { registerUser } from '../services/auth.service.js';
import { DuplicateEmailError, PasswordComplexityError } from '../errors/index.js';

const pool = getPool();
let passed = 0;
let failed = 0;

async function run() {
  // Test 1: Weak password rejected
  try {
    await registerUser(pool, { email: 'test@test.com', password: 'abc' });
    console.log('❌ Test 1 FAIL: weak password accepted');
    failed++;
  } catch (e) {
    if (e instanceof PasswordComplexityError) {
      console.log('✅ Test 1: Weak password rejected —', e.message);
      passed++;
    } else {
      console.log('❌ Test 1 FAIL:', e.message);
      failed++;
    }
  }

  // Test 2: Successful registration
  try {
    const user = await registerUser(pool, {
      email: 'operator@antigravity.io',
      password: 'Str0ng!Pass#2026',
      displayName: 'Operator One',
    });
    console.log('✅ Test 2: User registered —', JSON.stringify(user));
    passed++;
  } catch (e) {
    console.log('❌ Test 2 FAIL:', e.message);
    failed++;
  }

  // Test 3: Duplicate email blocked
  try {
    await registerUser(pool, {
      email: 'operator@antigravity.io',
      password: 'Another!Pass#2026',
    });
    console.log('❌ Test 3 FAIL: duplicate accepted');
    failed++;
  } catch (e) {
    if (e instanceof DuplicateEmailError) {
      console.log('✅ Test 3: Duplicate email blocked —', e.message);
      passed++;
    } else {
      console.log('❌ Test 3 FAIL:', e.message);
      failed++;
    }
  }

  // Test 4: Verify hash in DB starts with $argon2id$
  try {
    const res = await pool.query('SELECT password_hash FROM user_credentials LIMIT 1');
    const hash = res.rows[0].password_hash;
    if (hash.startsWith('$argon2id$')) {
      console.log('✅ Test 4: Hash prefix — ' + hash.substring(0, 30) + '...');
      passed++;
    } else {
      console.log('❌ Test 4 FAIL: unexpected prefix —', hash.substring(0, 20));
      failed++;
    }
  } catch (e) {
    console.log('❌ Test 4 FAIL:', e.message);
    failed++;
  }

  // Cleanup test user (CASCADE deletes credentials too)
  await pool.query("DELETE FROM users WHERE email = 'operator@antigravity.io'");
  console.log('🧹 Cleanup: test user removed');

  console.log(`\n── Results: ${passed} passed, ${failed} failed ──`);
  await closePool();
  process.exit(failed > 0 ? 1 : 0);
}

run().catch((e) => {
  console.error('FATAL:', e);
  process.exit(1);
});
