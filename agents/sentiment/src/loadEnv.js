// loadEnv.js — load the repo-root .env BEFORE any other module reads process.env.
//
// Why this exists: the Sentiment Agent's working directory is agents/sentiment
// (set by start_system.ps1), so the bare `dotenv/config` import only reads
// ./.env from that folder — which does NOT exist. The shared LLM_* keys
// (LLM_API_URL / LLM_API_KEY / LLM_MODEL / LLM_EFFORT) and the NEWSDATA_API_KEY /
// FINNHUB_API_KEY / REDIS_URL / KAFKA_* live in the repo-root .env. Without this,
// running the agent directly (`npm start`) leaves those unset and the LLM scorer
// is skipped. Mirrors how agents/deep-quant-loop/graph.py loads the repo .env.
//
// Import this module FIRST (before any module that reads env at load time). ES
// module imports are hoisted and executed in source order, so a first side-effect
// import guarantees this runs before the rest.
import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

// agents/sentiment/src -> repo root is three directories up (matches protoLoader).
dotenv.config({ path: path.resolve(here, '../../../.env') });

// Also load a local agents/sentiment/.env if one exists. dotenv does NOT override
// variables already set (by the repo-root load above or the parent shell that
// start_system.ps1 injects), so precedence stays: shell env > repo-root .env > local.
dotenv.config();
