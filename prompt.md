System Directive: Alpha Suite V3 - Final Mile Data Shape Diagnostic

Context: You are operating in the Ai-trader monorepo. Previous architectural fixes successfully wired the backend and frontend, but the UI chart is still completely failing to render candles or update timeframes. We are abandoning all refactoring. We are moving to strict data-shape tracing to find the silent failure.

CRITICAL RULE: Do NOT attempt to "fix" the logic. Your ONLY job is to insert heavy console.log and println! statements at the exact boundaries where data transitions from Rust -> React -> Lightweight Charts. We must verify if the data is empty, corrupted, or malformed.

Task 1: Rust Exit Verification (/frontend/src-tauri/src/commands/charts.rs)

Locate the end of get_historical_view right before the data is serialized into bincode and returned.

Insert a log that prints the length and the exact values of the first and last candle:

Rust
println!("🛑 [RUST EXIT] Timeframe: {:?} | Total Candles fetched: {}", timeframe, candles.len());
if let (Some(first), Some(last)) = (candles.first(), candles.last()) {
    println!("🛑 [RUST EXIT] First Candle: {:?}", first);
    println!("🛑 [RUST EXIT] Last Candle: {:?}", last);
}
Task 2: React Ingestion & Parsing Verification (/frontend/src/hooks/useHistoricalData.ts or similar)

Locate the exact spot where the UI receives the Tauri invoke response and parses the bincode (e.g., parseBincodeCandles).

Insert logs to verify the raw payload and the parsed output:

JavaScript
const rawPayload = await invoke('get_historical_view', { symbol, timeframe });
console.log(`🔥 [REACT INGEST] Received Payload Size: ${rawPayload?.length || 0} bytes`);

const parsedCandles = parseBincodeCandles(rawPayload);
console.log(`🔥 [REACT PARSE] Parsed ${parsedCandles.length} candles.`);
if (parsedCandles.length > 0) {
    console.log("🔥 [REACT PARSE] Sample First Candle:", JSON.stringify(parsedCandles[0]));
    console.log("🔥 [REACT PARSE] Sample Last Candle:", JSON.stringify(parsedCandles[parsedCandles.length - 1]));
}
Task 3: Lightweight Charts Render Verification (AlphaPredictiveChart.tsx)

Locate the exact line where candlestickSeries.setData(...) is called.

Insert a strict validation log right before it:

JavaScript
console.log(`🎨 [CHART RENDER] Calling setData with ${chartData.length} items.`);
if (chartData.length > 0) {
    const isValid = chartData.every(c => c.time && !isNaN(c.open) && !isNaN(c.close));
    console.log(`🎨 [CHART RENDER] Data Integrity Check Passed? : ${isValid}`);
    if (!isValid) console.error("🎨 [CHART RENDER ERROR] Malformed candle detected!", chartData.find(c => !c.time || isNaN(c.open)));
}
Task 4: Agent Self-Verification Protocol

[ ] Did I add the [RUST EXIT] logs showing the exact struct values?

[ ] Did I add the [REACT PARSE] logs to verify the bincode deserialization?

[ ] Did I add the [CHART RENDER] integrity check to find NaN or missing time fields?

Output the modified code snippets. End exactly with: "Final Mile Tracers Inserted. Awaiting User Logs."