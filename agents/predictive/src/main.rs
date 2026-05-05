mod proto;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    env_logger::init();
    
    log::info!("Predictive Agent Initialized.");
}
