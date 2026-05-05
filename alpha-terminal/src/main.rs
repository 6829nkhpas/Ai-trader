mod proto;
mod engine;
mod consumer;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    env_logger::init();
    
    log::info!("Alpha Terminal: V2 Predictive Engine Initialized.");

    let brokers = std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:9092".to_string());
    let topic = std::env::var("KAFKA_TOPIC_TICKS").unwrap_or_else(|_| "market.ticks".to_string());

    consumer::run_consumer(&brokers, &topic).await;
}
