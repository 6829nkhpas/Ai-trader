# Universal Data Contracts

Absolute serialization truth mapped from `/shared_protos`.

## 1. Tick (`market_data.proto`)
- `string symbol`
- `int64 timestamp_ms`
- `double last_traded_price`
- `int32 volume`
- `double best_bid`
- `double best_ask`

## 2. TechSignal (`technical_data.proto`)
- `string symbol`
- `int64 timestamp_ms`
- `double rsi_value`
- `double vwap_distance`
- `int32 technical_conviction_score`

## 3. NewsSentiment (`sentiment_data.proto`)
- `string symbol`
- `int64 timestamp_ms`
- `string headline`
- `int32 claude_conviction_score`
- `string reasoning_snippet`

## 4. AggregatedDecision (`decision.proto`)
- `string symbol`
- `int64 timestamp_ms`
- `int32 final_conviction_score`
- `double technical_weight_used`
- `double sentiment_weight_used`
- `ActionType action_type` (ENUM: `BUY = 0`, `SELL = 1`, `HOLD = 2`)
