// hasher.test.js — Unit tests for Argon2id hashing utility
// Run: AUTH_PEPPER=$(openssl rand -hex 32) node --test src/crypto/hasher.test.js

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { hashPassword, verifyPassword } from './hasher.js';

describe('Argon2id Hasher', () => {
  it('hash starts with $argon2id$', async () => {
    const hash = await hashPassword('TestPassword123!');
    assert.ok(hash.startsWith('$argon2id$'));
  });

  it('identical passwords produce different hashes (salt check)', async () => {
    const pw = 'IdenticalPassword99!';
    const h1 = await hashPassword(pw);
    const h2 = await hashPassword(pw);
    assert.notEqual(h1, h2);
  });

  it('correct password verifies true', async () => {
    const pw = 'CorrectHorse$Battery42';
    const hash = await hashPassword(pw);
    assert.equal(await verifyPassword(pw, hash), true);
  });

  it('wrong password verifies false', async () => {
    const hash = await hashPassword('RealPassword!1');
    assert.equal(await verifyPassword('WrongPassword!1', hash), false);
  });

  it('includes OWASP parameters in hash string', async () => {
    const hash = await hashPassword('ParamCheck!2024');
    assert.ok(hash.includes('m=65536'));
    assert.ok(hash.includes('t=3'));
    assert.ok(hash.includes('p=4'));
  });

  it('takes >50ms (memory-hard)', async () => {
    const start = performance.now();
    await hashPassword('TimingTest!456');
    const elapsed = performance.now() - start;
    assert.ok(elapsed > 50, `Only ${elapsed.toFixed(1)}ms`);
    console.log(`  Hash timing: ${elapsed.toFixed(1)}ms`);
  });
});
