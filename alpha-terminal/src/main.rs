mod proto;
mod engine;
mod consumer;
mod kafka_producer;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    env_logger::init();
    
    log::info!("Alpha Terminal: V2 Predictive Engine Initialized.");

    let brokers = std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:9092".to_string());
    let topic = std::env::var("KAFKA_TOPIC_TICKS").unwrap_or_else(|_| "market.ticks".to_string());
    let ohlc_topic = std::env::var("KAFKA_TOPIC_OHLC").unwrap_or_else(|_| "market.ohlc.10m".to_string());

    let producer = kafka_producer::init_producer(&brokers);

    consumer::run_consumer(&brokers, &topic, producer, &ohlc_topic).await;
}
