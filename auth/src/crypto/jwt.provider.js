// ──────────────────────────────────────────────────────────────
// crypto/jwt.provider.js — RS256 JWT engine
// Signs access tokens with an RSA private key and verifies
// with the corresponding public key. Embeds unique JTI for
// per-token revocation via Redis blacklist.
// ──────────────────────────────────────────────────────────────

import jwt from 'jsonwebtoken';
import { v4 as uuidv4 } from 'uuid';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config } from '../config.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Key Loading ─────────────────────────────────────────────
// Resolve paths relative to auth/ root (one level up from src/crypto/)
const authRoot = path.resolve(__dirname, '../..');

const privateKeyPath = path.resolve(authRoot, config.jwt.privateKeyPath);
const publicKeyPath = path.resolve(authRoot, config.jwt.publicKeyPath);

let _privateKey = null;
let _publicKey = null;

function loadPrivateKey() {
  if (!_privateKey) {
    try {
      _privateKey = fs.readFileSync(privateKeyPath, 'utf8');
    } catch (err) {
      console.error(`FATAL: Cannot read RS256 private key at ${privateKeyPath}: ${err.message}`);
      process.exit(1);
    }
  }
  return _privateKey;
}

function loadPublicKey() {
  if (!_publicKey) {
    try {
      _publicKey = fs.readFileSync(publicKeyPath, 'utf8');
    } catch (err) {
      console.error(`FATAL: Cannot read RS256 public key at ${publicKeyPath}: ${err.message}`);
      process.exit(1);
    }
  }
  return _publicKey;
}

// ── Sign Access Token ───────────────────────────────────────

/**
 * Signs an RS256 access token.
 * @param {{ sub: string, email: string, role: string, mfa_verified?: boolean }} claims
 * @returns {{ token: string, jti: string }}
 */
export function signAccessToken({ sub, email, role, mfa_verified = false }) {
  const jti = uuidv4();
  const token = jwt.sign(
    { sub, email, role, jti, mfa_verified },
    loadPrivateKey(),
    {
      algorithm: 'RS256',
      expiresIn: config.jwt.accessTtl,
      issuer: config.jwt.issuer,
    }
  );
  return { token, jti };
}

// ── Verify Access Token ─────────────────────────────────────

/**
 * Verifies an RS256 access token and returns decoded payload.
 * Throws on invalid/expired token.
 * @param {string} token
 * @returns {{ sub: string, email: string, role: string, jti: string, iat: number, exp: number, iss: string }}
 */
export function verifyAccessToken(token) {
  return jwt.verify(token, loadPublicKey(), {
    algorithms: ['RS256'],
    issuer: config.jwt.issuer,
  });
}

// ── Decode Without Verification ─────────────────────────────

/**
 * Decodes a JWT without verifying its signature.
 * Used to extract JTI from expired tokens during logout.
 * @param {string} token
 * @returns {Object|null} decoded payload or null
 */
export function decodeTokenUnsafe(token) {
  return jwt.decode(token);
}
