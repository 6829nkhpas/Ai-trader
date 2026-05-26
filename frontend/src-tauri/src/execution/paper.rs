use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VirtualPosition {
    pub id: String,
    pub symbol: String,
    pub side: String, // "BUY" or "SELL"
    pub entry_price: f64,
    pub quantity: f64,
    pub take_profit: f64,
    pub stop_loss: f64,
    pub status: String, // "OPEN", "CLOSED_WIN", "CLOSED_LOSS"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VirtualPortfolio {
    pub balance: f64,
    pub active_positions: Vec<VirtualPosition>,
    pub trade_history: Vec<VirtualPosition>,
}

#[tauri::command]
pub async fn execute_paper_trade(
    app: tauri::AppHandle,
    state: tauri::State<'_, std::sync::Mutex<VirtualPortfolio>>,
    symbol: String,
    side: String,
    entry_price: f64,
    stop_loss: f64,
    take_profit: f64,
) -> Result<String, String> {
    use tauri::Emitter;
    let mut portfolio = state.lock().map_err(|e| format!("State lock failed: {}", e))?;
    
    // Risk exactly 2% of total balance on the SL distance
    let risk_amount = portfolio.balance * 0.02;
    let sl_distance = (entry_price - stop_loss).abs();
    
    let quantity = if sl_distance > 1e-6 {
        (risk_amount / sl_distance).round()
    } else {
        10.0
    };
    
    let quantity = if quantity < 1.0 { 1.0 } else { quantity };

    let id = format!("{}-{}", symbol, chrono::Utc::now().timestamp_millis());
    let new_pos = VirtualPosition {
        id: id.clone(),
        symbol: symbol.clone(),
        side: side.clone(),
        entry_price,
        quantity,
        take_profit,
        stop_loss,
        status: "OPEN".to_string(),
    };

    portfolio.active_positions.push(new_pos);

    // Emit the update so frontend store receives it instantly
    let _ = app.emit("paper_portfolio_update", &*portfolio);

    log::info!(
        "[paper] Executed paper trade: {} | Symbol: {} | Side: {} | Qty: {} | Entry: ₹{} | SL: ₹{} | TP: ₹{}",
        id, symbol, side, quantity, entry_price, stop_loss, take_profit
    );

    Ok(format!(
        "Trade executed successfully! Deployed {} units of {} (Risking 2% on stop-loss distance).",
        quantity, symbol
    ))
}

#[tauri::command]
pub async fn get_paper_portfolio(
    state: tauri::State<'_, std::sync::Mutex<VirtualPortfolio>>,
) -> Result<VirtualPortfolio, String> {
    let portfolio = state.lock().map_err(|e| format!("State lock failed: {}", e))?;
    Ok(portfolio.clone())
}

pub fn process_tick_for_positions(
    app: &tauri::AppHandle,
    symbol: &str,
    price: f64,
) {
    use tauri::{Manager, Emitter};

    let Some(state) = app.try_state::<std::sync::Mutex<VirtualPortfolio>>() else {
        return;
    };
    
    let mut portfolio = match state.lock() {
        Ok(guard) => guard,
        Err(_) => return,
    };

    let symbol_upper = symbol.to_uppercase();
    let mut updated = false;

    let mut still_active = vec![];
    let active_positions = std::mem::take(&mut portfolio.active_positions);

    for mut pos in active_positions {
        if pos.symbol.to_uppercase() == symbol_upper && pos.status == "OPEN" {
            let mut closed = false;
            
            if pos.side == "BUY" {
                if price <= pos.stop_loss {
                    // Closed as LOSS
                    pos.status = "CLOSED_LOSS".to_string();
                    let loss = (pos.entry_price - price) * pos.quantity;
                    portfolio.balance -= loss;
                    closed = true;
                } else if price >= pos.take_profit {
                    // Closed as WIN
                    pos.status = "CLOSED_WIN".to_string();
                    let profit = (price - pos.entry_price) * pos.quantity;
                    portfolio.balance += profit;
                    closed = true;
                }
            } else if pos.side == "SELL" {
                if price >= pos.stop_loss {
                    // Closed as LOSS
                    pos.status = "CLOSED_LOSS".to_string();
                    let loss = (price - pos.entry_price) * pos.quantity;
                    portfolio.balance -= loss;
                    closed = true;
                } else if price <= pos.take_profit {
                    // Closed as WIN
                    pos.status = "CLOSED_WIN".to_string();
                    let profit = (pos.entry_price - price) * pos.quantity;
                    portfolio.balance += profit;
                    closed = true;
                }
            }

            if closed {
                updated = true;
                log::info!(
                    "[paper] Position closed: {} for {}. Status: {}. New Balance: ₹{}",
                    pos.id, symbol, pos.status, portfolio.balance
                );
                portfolio.trade_history.push(pos);
            } else {
                still_active.push(pos);
            }
        } else {
            still_active.push(pos);
        }
    }

    portfolio.active_positions = still_active;

    if updated {
        let _ = app.emit("paper_portfolio_update", &*portfolio);
    }
}
