mod proto;
mod engine;
mod consumer;
mod kafka_producer;
mod metrics;
mod ws_server;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    env_logger::init();

    log::info!("Alpha Terminal: V2 Predictive Engine Initialized.");

    // Started before any subsystem so /health answers during boot and a startup
    // failure below shows up as an unhealthy service rather than as a scrape
    // timeout with no explanation. AlphaMetrics degrades to an inert handle on
    // registry failure, so instrumentation can never take down the service.
    let metrics = metrics::AlphaMetrics::new();
    metrics.serve();

    let brokers = std::env::var("KAFKA_BROKERS")
        .or_else(|_| std::env::var("KAFKA_BROKER_URL"))
        .unwrap_or_else(|_| "localhost:19092".to_string());
    let topic = std::env::var("KAFKA_TOPIC_TICKS").unwrap_or_else(|_| "market.ticks".to_string());
    let ohlc_topic = std::env::var("KAFKA_TOPIC_OHLC").unwrap_or_else(|_| "market.ohlc.10m".to_string());

    // Capacity is per-client headroom before a slow subscriber is marked
    // `Lagged`. Every tick of every tracked symbol (~755) is one message, so a
    // market-open burst overran 100 within a few hundred ms.
    let (tx, _) = tokio::sync::broadcast::channel::<String>(4096);
    let snapshot = ws_server::new_snapshot();

    let tx_ws = tx.clone();
    let ws_metrics = metrics.clone();
    let ws_snapshot = snapshot.clone();
    tokio::spawn(async move {
        ws_server::start_server(8081, tx_ws.subscribe(), ws_snapshot, ws_metrics).await;
    });

    let producer = kafka_producer::init_producer(&brokers);

    consumer::run_consumer(&brokers, &topic, producer, &ohlc_topic, tx, snapshot, metrics).await;
}
