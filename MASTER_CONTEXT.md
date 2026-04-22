# GLOBAL ARCHITECTURE STATE
* **Goal**: Production-grade, low-latency AI trading terminal with a "Glass-Box" UI.
* **Infrastructure**: Docker Compose, Kafka (KRaft), QuestDB, Redis. Local-first execution, 100% cloud-ready.
* **Core Languages**: Rust (Ingestion/Math), Go/Node (API Workers), Next.js (Frontend).
* **Communication**: Protobufs (gRPC) & WebSockets.
* **Data Sources**: Zerodha Kite (Live Ticks), Marketaux/NewsData (Sentiment), Anthropic Claude 3 (NLP).
