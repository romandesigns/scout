use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

pub const SCHEMA_VERSION: &str = "scout.market-event.v1";
const BUCKET_SECONDS: f64 = 15.0;
const WARMUP_BUCKETS: usize = 8;
const KEEP_BUCKETS: usize = 160;
const SESSION_RESET_GAP_SECONDS: f64 = 6.0 * 60.0 * 60.0;
const CONTEXT_SECONDS: f64 = 15.0 * 60.0;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Payload {
    pub price: f64,
    pub size: f64,
    #[serde(default)] pub bid_price: f64,
    #[serde(default)] pub ask_price: f64,
    #[serde(default)] pub bid_size: f64,
    #[serde(default)] pub ask_size: f64,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MarketEvent {
    pub schema: String,
    pub event_type: String,
    pub symbol: String,
    pub source_ts: f64,
    pub received_ts: f64,
    pub sequence: u64,
    pub feed: String,
    pub payload: Payload,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Integrity { pub malformed: u64, pub duplicates: u64, pub out_of_order: u64 }
#[derive(Clone, Debug, Serialize)]
pub struct Candidate {
    pub ticker: String,
    pub detected_at: f64,
    pub price: f64,
    pub stage: &'static str,
    pub lifecycle_phase: &'static str,
    pub shadow_mode: bool,
    pub recipe_score: u8,
    pub recipe_present: Vec<&'static str>,
    pub recipe_missing: Vec<&'static str>,
    pub trigger_distance_pct: f64,
    pub base_extension_pct: f64,
    pub trigger_level: f64,
    pub invalidation_level: f64,
    pub trade_acceleration: f64,
    pub dollar_acceleration: f64,
    pub bid_ask_imbalance: f64,
    pub spread_pct: f64,
    pub confidence: u8,
    pub episode_id: u32,
    pub cross_sectional_percentile: f64,
    pub market_breadth_pct: f64,
    pub probability_5_before_3: f64,
    pub data_quality: &'static str,
}
#[derive(Clone, Debug, Serialize)]
pub struct ReplayReport {
    pub engine: &'static str,
    pub mode: &'static str,
    pub schema_version: &'static str,
    pub processed_events: usize,
    pub integrity: Integrity,
    pub candidates: Vec<Candidate>,
}

#[derive(Clone, Debug)]
struct Trade { ts: f64, price: f64, size: f64 }
#[derive(Clone, Debug)]
struct Quote { ts: f64, bid_price: f64, ask_price: f64, bid_size: f64, ask_size: f64 }
#[derive(Default)]
struct SymbolWindow {
    trades: VecDeque<Trade>,
    quotes: VecDeque<Quote>,
    last_eval: f64,
    armed: bool,
    phase: u8,
    episode_id: u32,
    last_transition_at: f64,
    episode_invalidation: f64,
    episode_peak: f64,
    last_confidence: u8,
    last_confidence_at: f64,
    current_bucket_start: Option<f64>,
    closed_bucket_count: usize,
    last_feed: Option<String>,
    last_trade_ts: Option<f64>,
}

impl SymbolWindow {
    fn reset_session_state(&mut self) {
        self.trades.clear();
        self.quotes.clear();
        self.last_eval = 0.0;
        self.armed = false;
        self.phase = 0;
        self.episode_id = 0;
        self.last_transition_at = 0.0;
        self.episode_invalidation = 0.0;
        self.episode_peak = 0.0;
        self.last_confidence = 0;
        self.last_confidence_at = 0.0;
        self.current_bucket_start = None;
        self.closed_bucket_count = 0;
        self.last_trade_ts = None;
        self.last_feed = None;
    }

    fn observe_bucket(&mut self, ts: f64) {
        let bucket_start = ts - ts.rem_euclid(BUCKET_SECONDS);
        match self.current_bucket_start {
            None => self.current_bucket_start = Some(bucket_start),
            Some(current) if bucket_start > current => {
                let crossed = ((bucket_start - current) / BUCKET_SECONDS).round() as usize;
                self.closed_bucket_count = (self.closed_bucket_count + crossed).min(KEEP_BUCKETS);
                self.current_bucket_start = Some(bucket_start);
            }
            _ => {}
        }
    }

    fn should_reset_for_event(&self, event: &MarketEvent) -> bool {
        let entering_overnight = self.last_feed.as_deref().is_some_and(|feed| feed != "boats")
            && event.feed == "boats";
        let large_gap = self.last_trade_ts
            .is_some_and(|last_ts| event.source_ts - last_ts >= SESSION_RESET_GAP_SECONDS);
        entering_overnight || large_gap
    }
}
pub fn load_events(path: &Path) -> Result<(Vec<MarketEvent>, Integrity), String> {
    let file = File::open(path).map_err(|error| error.to_string())?;
    let mut events = Vec::new();
    let mut integrity = Integrity::default();
    let mut seen = HashSet::new();
    let mut last_ts = 0.0_f64;
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim().is_empty() { continue; }
        let event: MarketEvent = match serde_json::from_str(&line) {
            Ok(event) => event,
            Err(error) => {
                integrity.malformed += 1;
                return Err(format!(
                    "invalid event at line {} (malformed={}): {}",
                    index + 1,
                    integrity.malformed,
                    error
                ));
            }
        };
        if validate_event(&event).is_err() {
            integrity.malformed += 1;
            return Err(format!(
                "invalid event contract at line {} (malformed={})",
                index + 1,
                integrity.malformed
            ));
        }
        if !seen.insert((event.feed.clone(), event.sequence)) { integrity.duplicates += 1; continue; }
        if event.source_ts < last_ts { integrity.out_of_order += 1; }
        last_ts = last_ts.max(event.source_ts);
        events.push(event);
    }
    events.sort_by(|a, b| a.source_ts.total_cmp(&b.source_ts).then(a.sequence.cmp(&b.sequence)));
    Ok((events, integrity))
}
fn pct(from: f64, to: f64) -> f64 { if from == 0.0 { 0.0 } else { (to - from) / from * 100.0 } }
#[derive(Debug)]
struct Evaluation { candidate: Option<Candidate>, phase: u8 }

#[cfg(test)]
fn update_armed_state(armed: bool, qualified: bool, continuity_holds: bool) -> (bool, bool) {
    let emit = qualified && !armed;
    let armed_after = qualified || (armed && continuity_holds);
    (emit, armed_after)
}

fn evaluate(symbol: &str, window: &SymbolWindow) -> Evaluation {
    let Some(latest) = window.trades.back() else { return Evaluation { candidate: None, phase: 0 }; };
    // Python Scout warms on closed 15-second buckets, not raw trade count.
    // Empty intervals count as closed buckets, so sparse symbols can become
    // evaluable after enough elapsed market structure exists.
    if window.closed_bucket_count < WARMUP_BUCKETS { return Evaluation { candidate: None, phase: 0 }; }
    let base_cutoff = latest.ts - CONTEXT_SECONDS;
    let trigger_cutoff = latest.ts - 5.0;
    let base: Vec<&Trade> = window.trades.iter().filter(|trade| trade.ts >= base_cutoff).collect();
    // Structure is local; the longer 15-minute window is only the activity baseline.
    // Using the whole context for resistance made an old premarket spike suppress a
    // genuinely new regular-session base and trigger.
    let structure: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts >= latest.ts - 300.0).collect();
    let prior: Vec<&Trade> = structure.iter().copied().filter(|trade| trade.ts < trigger_cutoff).collect();
    if prior.is_empty() { return Evaluation { candidate: None, phase: 0 }; }
    let base_low = structure.iter().map(|trade| trade.price).fold(f64::INFINITY, f64::min);
    let base_high = structure.iter().map(|trade| trade.price).fold(f64::NEG_INFINITY, f64::max);
    let trigger = prior.iter().map(|trade| trade.price).fold(f64::NEG_INFINITY, f64::max);
    let range_pct = pct(base_low, base_high);
    let extension = pct(base_low, latest.price);
    let trigger_distance = pct(latest.price, trigger);
    let trades15: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts >= latest.ts - 15.0).collect();
    let trades30: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts >= latest.ts - 30.0).collect();
    let volume15: f64 = trades15.iter().map(|trade| trade.size).sum();
    let dollar15: f64 = trades15.iter().map(|trade| trade.price * trade.size).sum();
    let dollar30: f64 = trades30.iter().map(|trade| trade.price * trade.size).sum();
    // Ticker-relative dormant baseline: compare the newest 15/30 seconds with the
    // same symbol's preceding context, excluding the active 30-second window.
    let historical: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts < latest.ts - 30.0).collect();
    let historical_seconds = (latest.ts - 30.0 - base_cutoff).max(30.0);
    let baseline_trades30 = (historical.len() as f64 / historical_seconds * 30.0).max(0.5);
    let historical_dollar: f64 = historical.iter().map(|trade| trade.price * trade.size).sum();
    let baseline_dollar30 = (historical_dollar / historical_seconds * 30.0).max(100.0);
    let trade_acceleration = trades30.len() as f64 / baseline_trades30;
    let dollar_acceleration = dollar30 / baseline_dollar30;
    let change5 = base.iter().find(|trade| trade.ts >= latest.ts - 5.0).map(|trade| pct(trade.price, latest.price)).unwrap_or(0.0);
    let change15 = trades15.first().map(|trade| pct(trade.price, latest.price)).unwrap_or(0.0);
    let quote = window.quotes.back().filter(|quote| latest.ts - quote.ts <= 10.0);
    let bid_ask_imbalance = quote.map(|q| q.bid_size / q.ask_size.max(1.0)).unwrap_or(1.0);
    let spread_pct = quote.map(|q| pct(q.bid_price, q.ask_price).abs()).unwrap_or(0.0);
    let quote_support = quote.is_some_and(|q| q.bid_price > 0.0 && q.ask_price > q.bid_price && bid_ask_imbalance >= 1.15 && spread_pct <= 3.0);
    let checks = [
        ("compressed or orderly base", range_pct <= 3.5),
        ("price remains near the base", extension <= 3.0),
        ("pressing a nearby trigger", (-3.0..=2.0).contains(&trigger_distance)),
        ("EMA structure is improving", change15 >= 0.0),
        ("trade frequency is accelerating", trade_acceleration >= 3.0),
        ("dollar volume is accelerating", dollar_acceleration >= 3.0),
        ("participation is broadening", trades15.len() >= 3 && volume15 > 0.0),
        ("bid pressure supports the move", quote_support),
        ("price or volume is accelerating", change5 > 0.0 || dollar15 > dollar30 * 0.55),
        ("path avoids bearish failure", change15 > -0.2),
    ];
    let present: Vec<_> = checks.iter().filter_map(|(name, yes)| yes.then_some(*name)).collect();
    let missing: Vec<_> = checks.iter().filter_map(|(name, yes)| (!yes).then_some(*name)).collect();
    let score = ((present.len() as f64 / checks.len() as f64) * 10.0).round() as u8;
    let stirring = trades15.len() >= 3 && dollar15 >= 300.0 && trade_acceleration >= 3.0
        && dollar_acceleration >= 2.0 && change15 > -0.2 && extension <= 3.0;
    let shaping = stirring && trades30.len() >= 6 && dollar30 >= 750.0
        && dollar_acceleration >= 3.0 && (-3.0..=2.0).contains(&trigger_distance)
        && (quote_support || change5 > 0.05 || change15 > 0.15);
    let phase = if shaping { 2 } else if stirring { 1 } else { 0 };
    let confidence = ((score as u16 * 7
        + trade_acceleration.min(10.0).round() as u16
        + dollar_acceleration.min(10.0).round() as u16
        + if quote_support { 10 } else { 0 }).min(100)) as u8;
    // Conservative prior, intentionally bounded until prospective outcomes can
    // calibrate it. This is an estimate, never a promise of profitability.
    let probability_5_before_3 = (0.18 + confidence as f64 * 0.005
        + if quote_support { 0.05 } else { 0.0 }
        - (spread_pct - 1.0).max(0.0) * 0.02).clamp(0.05, 0.85);
    let candidate = (phase > 0).then(|| Candidate {
        ticker: symbol.to_string(), detected_at: latest.ts, price: latest.price,
        stage: if shaping && window.episode_id > 0 { "REARMED" } else if shaping { "SHAPING_UP" } else { "STIRRING" },
        lifecycle_phase: if shaping && window.episode_id > 0 { "REARMED" } else if shaping { "TRIGGER_READY" } else { "STIRRING" },
        shadow_mode: !shaping, recipe_score: score, recipe_present: present,
        recipe_missing: missing, trigger_distance_pct: trigger_distance,
        base_extension_pct: extension, trigger_level: trigger,
        invalidation_level: base_low, trade_acceleration, dollar_acceleration,
        bid_ask_imbalance, spread_pct, confidence,
        episode_id: window.episode_id, cross_sectional_percentile: 0.0,
        market_breadth_pct: 0.0, probability_5_before_3,
        data_quality: if quote.is_some() { "TRADES_QUOTES" } else { "TRADES_ONLY" },
    });
    Evaluation { candidate, phase }
}
pub struct Engine {
    states: HashMap<String, SymbolWindow>,
    pub processed_events: usize,
}

impl Default for Engine {
    fn default() -> Self {
        Self { states: HashMap::new(), processed_events: 0 }
    }
}

impl Engine {
    /// Process one already-validated market event and return only a fresh
    /// qualification edge. The state machine is shared by replay and live
    /// streaming so production perception cannot silently diverge from the
    /// frozen calibration core.
    pub fn process_event(&mut self, event: MarketEvent) -> Option<Candidate> {
        self.processed_events += 1;
        let now = event.source_ts;
        let mut emitted = {
            let state = self.states.entry(event.symbol.clone()).or_default();
            if state.should_reset_for_event(&event) { state.reset_session_state(); }
            state.last_feed = Some(event.feed.clone());
            if event.event_type == "trade" {
                state.observe_bucket(now);
                state.last_trade_ts = Some(now);
                state.episode_peak = state.episode_peak.max(event.payload.price);
                state.trades.push_back(Trade { ts: now, price: event.payload.price, size: event.payload.size });
            } else {
                state.quotes.push_back(Quote {
                    ts: now, bid_price: event.payload.bid_price, ask_price: event.payload.ask_price,
                    bid_size: event.payload.bid_size, ask_size: event.payload.ask_size,
                });
            }
            while state.trades.front().is_some_and(|trade| trade.ts < now - CONTEXT_SECONDS) { state.trades.pop_front(); }
            while state.quotes.front().is_some_and(|quote| quote.ts < now - 30.0) { state.quotes.pop_front(); }
            if now - state.last_eval < 1.0 { return None; }
            state.last_eval = now;

            let latest_price = state.trades.back().map(|trade| trade.price).unwrap_or(event.payload.price);
            let elapsed = now - state.last_transition_at;
            let drawdown = if state.episode_peak > 0.0 { -pct(state.episode_peak, latest_price) } else { 0.0 };
            let invalidated = state.episode_invalidation > 0.0 && latest_price <= state.episode_invalidation;
            let episode_finished = (state.phase == 1 && elapsed >= 300.0)
                || (state.phase == 2 && elapsed >= 60.0 && invalidated)
                || (state.phase == 2 && elapsed >= 900.0 && drawdown >= 4.0);
            if episode_finished {
                state.phase = 0;
                state.armed = false;
                state.episode_id = state.episode_id.saturating_add(1);
                state.episode_peak = latest_price;
                state.episode_invalidation = 0.0;
            }

            let evaluation = evaluate(&event.symbol, state);
            state.last_confidence = evaluation.candidate.as_ref().map(|c| c.confidence).unwrap_or(0);
            state.last_confidence_at = now;
            let emit = evaluation.phase > state.phase;
            if evaluation.phase > 0 { state.phase = state.phase.max(evaluation.phase); }
            state.armed = state.phase > 0;
            if emit {
                state.last_transition_at = now;
                if let Some(candidate) = evaluation.candidate.as_ref() {
                    state.episode_invalidation = candidate.invalidation_level;
                    state.episode_peak = state.episode_peak.max(candidate.price);
                }
                evaluation.candidate
            } else { None }
        };

        if let Some(candidate) = emitted.as_mut() {
            let recent: Vec<&SymbolWindow> = self.states.values()
                .filter(|state| now - state.last_confidence_at <= 60.0 && state.last_confidence > 0)
                .collect();
            if !recent.is_empty() {
                let at_or_below = recent.iter().filter(|state| state.last_confidence <= candidate.confidence).count();
                candidate.cross_sectional_percentile = at_or_below as f64 / recent.len() as f64 * 100.0;
                let shaping = recent.iter().filter(|state| state.last_confidence >= 65).count();
                candidate.market_breadth_pct = shaping as f64 / recent.len() as f64 * 100.0;
            }
        }
        emitted
    }
}

pub fn validate_event(event: &MarketEvent) -> Result<(), String> {
    if event.schema != SCHEMA_VERSION
        || !matches!(event.event_type.as_str(), "trade" | "quote")
        || event.source_ts <= 0.0
        || event.payload.price <= 0.0
        || event.payload.size < 0.0
        || (event.event_type == "quote"
            && (event.payload.bid_price <= 0.0 || event.payload.ask_price <= event.payload.bid_price))
    {
        return Err("invalid event contract".to_string());
    }
    Ok(())
}

pub fn parse_event_line(line: &str) -> Result<MarketEvent, String> {
    let event: MarketEvent = serde_json::from_str(line).map_err(|error| error.to_string())?;
    validate_event(&event)?;
    Ok(event)
}

pub fn run(events: Vec<MarketEvent>, integrity: Integrity) -> ReplayReport {
    let mut engine = Engine::default();
    let mut candidates = Vec::new();
    for event in events {
        if let Some(candidate) = engine.process_event(event) {
            candidates.push(candidate);
        }
    }
    ReplayReport {
        engine: "scout-market-replay-rust/0.1.0",
        mode: "SIMULATION",
        schema_version: SCHEMA_VERSION,
        processed_events: engine.processed_events,
        integrity,
        candidates,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn pct_is_directional() { assert!((pct(10.0, 10.2) - 2.0).abs() < 0.000001); }
    #[test]
    fn empty_replay_is_isolated() {
        let report = run(Vec::new(), Integrity::default());
        assert_eq!(report.mode, "SIMULATION");
        assert_eq!(report.processed_events, 0);
    }
    #[test]
    fn malformed_input_fails_with_counted_integrity() {
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("scout-market-replay-malformed-{nonce}.ndjson"));
        fs::write(&path, "{not-json}\n").unwrap();
        let error = load_events(&path).expect_err("malformed replay input must fail");
        let _ = fs::remove_file(path);
        assert!(error.contains("malformed=1"), "unexpected error: {error}");
    }
    #[test]
    fn flat_sparse_history_warms_without_false_wakeup() {
        let mut events = Vec::new();
        for index in 0..9_u64 {
            let ts = 1_000.0 + index as f64 * 15.0;
            events.push(MarketEvent {
                schema: SCHEMA_VERSION.to_string(),
                event_type: "trade".to_string(),
                symbol: "TEST".to_string(),
                source_ts: ts,
                received_ts: ts,
                sequence: index,
                feed: "sip".to_string(),
                payload: Payload { price: 1.0, size: 100.0, ..Payload::default() },
            });
        }
        let report = run(events, Integrity::default());
        assert!(report.candidates.is_empty(), "a flat sparse ticker must warm without becoming a wake-up alert");
    }

    #[test]
    fn dormant_symbol_emits_stirring_then_shaping_up_on_acceleration() {
        let mut engine = Engine::default();
        let mut stages = Vec::new();
        for index in 0..9_u64 {
            let ts = 5_000.0 + index as f64 * 15.0;
            let event = MarketEvent {
                schema: SCHEMA_VERSION.to_string(), event_type: "trade".to_string(),
                symbol: "WAKE".to_string(), source_ts: ts, received_ts: ts,
                sequence: index, feed: "sip".to_string(),
                payload: Payload { price: 1.0, size: 100.0, ..Payload::default() },
            };
            if let Some(candidate) = engine.process_event(event) { stages.push(candidate.stage); }
        }
        let quote_ts = 5_122.0;
        let quote = MarketEvent {
            schema: SCHEMA_VERSION.to_string(), event_type: "quote".to_string(),
            symbol: "WAKE".to_string(), source_ts: quote_ts, received_ts: quote_ts,
            sequence: 100, feed: "sip".to_string(), payload: Payload {
                price: 1.005, size: 0.0, bid_price: 1.0, ask_price: 1.01,
                bid_size: 800.0, ask_size: 200.0,
            },
        };
        let _ = engine.process_event(quote);
        for index in 0..8_u64 {
            let ts = 5_123.0 + index as f64;
            let event = MarketEvent {
                schema: SCHEMA_VERSION.to_string(), event_type: "trade".to_string(),
                symbol: "WAKE".to_string(), source_ts: ts, received_ts: ts,
                sequence: 200 + index, feed: "sip".to_string(),
                payload: Payload { price: 1.0 + index as f64 * 0.0005, size: 150.0, ..Payload::default() },
            };
            if let Some(candidate) = engine.process_event(event) { stages.push(candidate.stage); }
        }
        assert_eq!(stages, vec!["STIRRING", "SHAPING_UP"]);
    }

    #[test]
    fn entering_overnight_resets_bucket_warmup() {
        let mut state = SymbolWindow::default();
        state.last_feed = Some("sip".to_string());
        state.last_trade_ts = Some(10_000.0);
        state.closed_bucket_count = WARMUP_BUCKETS;
        let event = MarketEvent {
            schema: SCHEMA_VERSION.to_string(),
            event_type: "trade".to_string(),
            symbol: "TEST".to_string(),
            source_ts: 10_001.0,
            received_ts: 10_001.0,
            sequence: 1,
            feed: "boats".to_string(),
            payload: Payload { price: 1.0, size: 1.0, ..Payload::default() },
        };
        assert!(state.should_reset_for_event(&event));
        state.reset_session_state();
        assert_eq!(state.closed_bucket_count, 0);
        assert!(state.trades.is_empty());
    }

    #[test]
    fn recipe_flicker_does_not_rearm_while_structure_holds() {
        let (emit_first, armed) = update_armed_state(false, true, true);
        assert!(emit_first);
        assert!(armed);
        let (emit_dip, armed) = update_armed_state(armed, false, true);
        assert!(!emit_dip);
        assert!(armed, "temporary recipe-score/participation flicker must preserve the episode");
        let (emit_recover, armed) = update_armed_state(armed, true, true);
        assert!(!emit_recover, "requalification inside the same structural watch must not emit a new episode");
        assert!(armed);
    }

    #[test]
    fn qualified_state_stays_active_even_when_continuity_subset_flickers() {
        let (_, armed) = update_armed_state(false, true, true);
        let (emit_qualified_flicker, armed) = update_armed_state(armed, true, false);
        assert!(!emit_qualified_flicker, "an already-active qualified state must not emit again");
        assert!(armed, "qualification itself keeps the active episode alive even if the continuity subset flickers");
        let (emit_next, armed) = update_armed_state(armed, true, true);
        assert!(!emit_next, "continuous qualification must remain a single emission edge");
        assert!(armed);
    }

    #[test]
    fn structural_break_allows_a_fresh_episode() {
        let (_, armed) = update_armed_state(false, true, true);
        let (_, armed) = update_armed_state(armed, false, false);
        assert!(!armed);
        let (emit_recover, armed) = update_armed_state(armed, true, true);
        assert!(emit_recover);
        assert!(armed);
    }

    #[test]
    fn stateful_engine_matches_batch_replay_candidate_count() {
        let mut events = Vec::new();
        for index in 0..12_u64 {
            let ts = 2_000.0 + index as f64 * 15.0;
            events.push(MarketEvent {
                schema: SCHEMA_VERSION.to_string(),
                event_type: "trade".to_string(),
                symbol: "STREAM".to_string(),
                source_ts: ts,
                received_ts: ts,
                sequence: index,
                feed: "sip".to_string(),
                payload: Payload { price: 1.0, size: 100.0, ..Payload::default() },
            });
        }
        let batch = run(events.clone(), Integrity::default());
        let mut engine = Engine::default();
        let streamed: Vec<Candidate> = events.into_iter().filter_map(|event| engine.process_event(event)).collect();
        assert_eq!(streamed.len(), batch.candidates.len());
        assert_eq!(engine.processed_events, batch.processed_events);
    }

    #[test]
    fn stream_line_uses_same_market_event_contract() {
        let line = r#"{"schema":"scout.market-event.v1","event_type":"trade","symbol":"TEST","source_ts":1000.0,"received_ts":1000.1,"sequence":1,"feed":"sip","payload":{"price":1.25,"size":50.0}}"#;
        let event = parse_event_line(line).expect("valid live JSONL event");
        assert_eq!(event.symbol, "TEST");
        assert_eq!(event.payload.price, 1.25);
    }

}
