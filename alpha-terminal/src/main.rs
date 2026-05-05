mod proto;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    env_logger::init();
    
    log::info!("Alpha Terminal: V2 Predictive Engine Initialized.");
}
