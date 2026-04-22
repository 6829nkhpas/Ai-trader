// kafkaProducer.js — Kafka producer for the Sentiment Agent.
//
// Uses KafkaJS (^2) to publish binary-encoded NewsSentiment Protobuf messages
// to the `signals.sentiment` topic.
//
// Architecture:
//   initProducer()      → creates + connects the KafkaJS producer (call once)
//   publishSentiment()  → encodes NewsSentiment via protobufjs + sends to Kafka
//   disconnectProducer() → graceful shutdown (call on SIGTERM/SIGINT)
//
// Message key = symbol — ensures all signals for the same symbol land on the
// same Kafka partition, preserving causal ordering for the aggregator.
//
// Required env vars:
//   KAFKA_BROKER_URL         — broker address (default: localhost:9092)
//   KAFKA_TOPIC_SENTIMENT    — topic name    (default: signals.sentiment)
//   KAFKA_CLIENT_ID_SENTIMENT — Kafka client ID (default: sentiment-agent)

import { Kafka, CompressionTypes } from 'kafkajs';
import { encodeNewsSentiment } from './protoLoader.js';

// ── Module-level producer instance (initialised once) ────────────────────────

let _producer = null;

// ── initProducer ─────────────────────────────────────────────────────────────

/**
 * Creates and connects a KafkaJS producer.
 * Must be called once at startup before any `publishSentiment` calls.
 *
 * @returns {Promise<void>}
 */
export async function initProducer() {
  const brokers  = (process.env.KAFKA_BROKER_URL ?? 'localhost:9092').split(',');
  const clientId = process.env.KAFKA_CLIENT_ID_SENTIMENT ?? 'sentiment-agent';

  const kafka = new Kafka({
    clientId,
    brokers,
    // Retry aggressively for transient broker unavailability.
    retry: {
      initialRetryTime: 300,
      retries: 5,
    },
  });

  _producer = kafka.producer({
    // Wait up to 5 ms to batch multiple signals — minimal latency impact.
    linger: 5,
    // Compress with GZIP — news text payloads compress well.
    compression: CompressionTypes.GZIP,
  });

  await _producer.connect();
  console.log(
    `[kafkaProducer] Connected. brokers=${brokers.join(',')} clientId=${clientId}`
  );
}

// ── publishSentiment ─────────────────────────────────────────────────────────

/**
 * Encodes a NewsSentiment payload as a Protobuf binary and publishes it to Kafka.
 *
 * @param {import('protobufjs').Type} NewsSentiment - Loaded Protobuf message type.
 * @param {Object} data - The sentiment data to publish.
 * @param {string}  data.symbol                  - NSE ticker symbol.
 * @param {number}  data.timestamp_ms            - Unix epoch milliseconds.
 * @param {string}  data.headline                - Original news headline.
 * @param {number}  data.claude_conviction_score - Claude's conviction score (1-100).
 * @param {string}  data.reasoning_snippet       - Short Claude reasoning excerpt.
 * @returns {Promise<void>}
 */
export async function publishSentiment(NewsSentiment, data) {
  if (!_producer) {
    console.error('[kafkaProducer] Producer not initialised — call initProducer() first.');
    return;
  }

  const topic = process.env.KAFKA_TOPIC_SENTIMENT ?? 'signals.sentiment';

  // Encode to Protobuf binary (Uint8Array → Buffer for KafkaJS).
  let payload;
  try {
    payload = Buffer.from(encodeNewsSentiment(NewsSentiment, data));
  } catch (err) {
    console.error(`[kafkaProducer] Protobuf encode failed for symbol='${data.symbol}': ${err.message}`);
    return;
  }

  try {
    const result = await _producer.send({
      topic,
      messages: [
        {
          // Partition key = symbol — preserves per-symbol ordering.
          key:   data.symbol,
          value: payload,
        },
      ],
    });

    const meta = result[0];
    console.log(
      `[kafkaProducer] Published: symbol=${data.symbol}  ` +
      `score=${data.claude_conviction_score}  ` +
      `topic=${topic}  partition=${meta.partition}  offset=${meta.baseOffset}`
    );
  } catch (err) {
    // Non-fatal — log and continue; individual publish failures don't stop the loop.
    console.error(
      `[kafkaProducer] Failed to publish for symbol='${data.symbol}': ${err.message}`
    );
  }
}

// ── disconnectProducer ───────────────────────────────────────────────────────

/**
 * Gracefully disconnects the Kafka producer.
 * Call this in SIGTERM / SIGINT handlers to flush pending messages.
 *
 * @returns {Promise<void>}
 */
export async function disconnectProducer() {
  if (_producer) {
    await _producer.disconnect();
    console.log('[kafkaProducer] Disconnected cleanly.');
    _producer = null;
  }
}
