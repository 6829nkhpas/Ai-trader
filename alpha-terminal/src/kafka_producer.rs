use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::ClientConfig;
use prost::Message as ProstMessage;
use crate::proto::market_data::OhlcCandle;

pub fn init_producer(brokers: &str) -> FutureProducer {
    ClientConfig::new()
        .set("bootstrap.servers", brokers)
        .set("message.timeout.ms", "5000")
        .set("queue.buffering.max.ms", "5")
        .create()
        .expect("Producer creation error")
}

/// Publishes a completed candle to `topic`.
///
/// Returns `true` if the broker acknowledged the record. The candle is still
/// dropped on failure, exactly as before; the bool exists only so the caller can
/// count the loss into `alpha_terminal_publish_errors_total`. Without it a
/// broker outage silently discards every candle this service closes and the only
/// trace is the log stream.
pub async fn publish_candle(producer: &FutureProducer, topic: &str, candle: &OhlcCandle) -> bool {
    let mut encoded = Vec::new();
    candle.encode(&mut encoded).expect("Failed to encode OhlcCandle");

    let record: FutureRecord<'_, str, [u8]> = FutureRecord::to(topic)
        .payload(encoded.as_slice())
        .key(candle.symbol.as_str());

    match producer.send(record, rdkafka::util::Timeout::Never).await {
        Ok((partition, offset)) => {
            log::debug!(
                "Successfully published OhlcCandle for {} to partition {} at offset {}",
                candle.symbol,
                partition,
                offset
            );
            true
        }
        Err((e, _)) => {
            log::error!("Failed to publish OhlcCandle for {}: {:?}", candle.symbol, e);
            false
        }
    }
}
