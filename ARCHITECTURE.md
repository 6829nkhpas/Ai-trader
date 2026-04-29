# System Architecture & Technical Stack

## Monorepo Directory Tree
- `/ingestion` - Market data ingestion services
- `/agents/technical` - Quantitative technical analysis agent
- `/agents/sentiment` - NLP/LLM-based news sentiment agent
- `/aggregator` - Core decision fusion engine
- `/frontend` - Glass-Box trading UI
- `/shared_protos` - Universal Protobuf data contracts

## Tech Stack
- **Ingestion & Math/Aggregator**: Rust (tokio, rdkafka) - Low latency and high performance
- **Sentiment Agent**: Node.js - Seamless interaction with Anthropic/Marketaux APIs
- **Frontend**: Next.js - Real-time WebSocket streaming and responsive UI

## Kafka Topic Routing
- `live_ticks` → **Technical Agent** → `technical_signals`
- `live_ticks` / `news_feed` → **Sentiment Agent** → `sentiment_signals`
- `technical_signals` + `sentiment_signals` → **Aggregator Engine** → `aggregated_decisions`
- `aggregated_decisions` → **Frontend (via WebSocket)** / **Execution Layer**

## V2 Objective
**Transitioning from Reactive (V1) to Predictive (V2 Alpha Suite).**
