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

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Payload { pub price: f64, pub size: f64 }
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
#[derive(Default)]
struct SymbolWindow {
    trades: VecDeque<Trade>,
    last_eval: f64,
    armed: bool,
    current_bucket_start: Option<f64>,
    closed_bucket_count: usize,
    last_feed: Option<String>,
    last_trade_ts: Option<f64>,
}

impl SymbolWindow {
    fn reset_session_state(&mut self) {
        self.trades.clear();
        self.last_eval = 0.0;
        self.armed = false;
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
        if event.schema != SCHEMA_VERSION || event.event_type != "trade" || event.source_ts <= 0.0 || event.payload.price <= 0.0 || event.payload.size < 0.0 {
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
struct Evaluation { candidate: Option<Candidate>, continuity_holds: bool }

fn update_armed_state(armed: bool, qualified: bool, continuity_holds: bool) -> (bool, bool) {
    let emit = qualified && !armed;
    let armed_after = qualified || (armed && continuity_holds);
    (emit, armed_after)
}

fn evaluate(symbol: &str, window: &SymbolWindow) -> Evaluation {
    let Some(latest) = window.trades.back() else { return Evaluation { candidate: None, continuity_holds: false }; };
    // Python Scout warms on closed 15-second buckets, not raw trade count.
    // Empty intervals count as closed buckets, so sparse symbols can become
    // evaluable after enough elapsed market structure exists.
    if window.closed_bucket_count < WARMUP_BUCKETS { return Evaluation { candidate: None, continuity_holds: false }; }
    let base_cutoff = latest.ts - 300.0;
    let trigger_cutoff = latest.ts - 5.0;
    let base: Vec<&Trade> = window.trades.iter().filter(|trade| trade.ts >= base_cutoff).collect();
    let prior: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts < trigger_cutoff).collect();
    if prior.is_empty() { return Evaluation { candidate: None, continuity_holds: false }; }
    let base_low = base.iter().map(|trade| trade.price).fold(f64::INFINITY, f64::min);
    let base_high = base.iter().map(|trade| trade.price).fold(f64::NEG_INFINITY, f64::max);
    let trigger = prior.iter().map(|trade| trade.price).fold(f64::NEG_INFINITY, f64::max);
    let range_pct = pct(base_low, base_high);
    let extension = pct(base_low, latest.price);
    let trigger_distance = pct(latest.price, trigger);
    let trades15: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts >= latest.ts - 15.0).collect();
    let trades30: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts >= latest.ts - 30.0).collect();
    let volume15: f64 = trades15.iter().map(|trade| trade.size).sum();
    let volume30: f64 = trades30.iter().map(|trade| trade.size).sum();
    let change5 = base.iter().find(|trade| trade.ts >= latest.ts - 5.0).map(|trade| pct(trade.price, latest.price)).unwrap_or(0.0);
    let change15 = trades15.first().map(|trade| pct(trade.price, latest.price)).unwrap_or(0.0);
    let checks = [
        ("compressed or orderly base", range_pct <= 3.5),
        ("price remains near the base", extension <= 0.75),
        ("pressing a nearby trigger", (-0.35..=0.75).contains(&trigger_distance)),
        ("EMA structure is improving", change15 >= 0.0),
        ("relative volume is waking up", volume15 * 2.0 >= volume30.max(1.0)),
        ("participation is broadening", trades15.len() >= 3 && volume15 > 0.0),
        ("price or volume is accelerating", change5 > 0.0 || volume15 > volume30 * 0.55),
        ("path avoids bearish failure", change15 > -0.2),
    ];
    let present: Vec<_> = checks.iter().filter_map(|(name, yes)| yes.then_some(*name)).collect();
    let missing: Vec<_> = checks.iter().filter_map(|(name, yes)| (!yes).then_some(*name)).collect();
    let score = ((present.len() as f64 / checks.len() as f64) * 10.0).round() as u8;
    let continuity_holds = checks[0].1 && checks[1].1 && checks[2].1 && checks[3].1 && checks[4].1 && checks[7].1;
    let qualified = score >= 7 && extension <= 0.75 && (-0.35..=0.75).contains(&trigger_distance);
    let candidate = qualified.then(|| Candidate { ticker: symbol.to_string(), detected_at: latest.ts, price: latest.price, stage: "PRE_IGNITION", lifecycle_phase: "ARMED", shadow_mode: true, recipe_score: score, recipe_present: present, recipe_missing: missing, trigger_distance_pct: trigger_distance, base_extension_pct: extension });
    Evaluation { candidate, continuity_holds }
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
        let state = self.states.entry(event.symbol.clone()).or_default();
        if state.should_reset_for_event(&event) {
            state.reset_session_state();
        }
        state.observe_bucket(event.source_ts);
        state.last_feed = Some(event.feed.clone());
        state.last_trade_ts = Some(event.source_ts);
        state.trades.push_back(Trade { ts: event.source_ts, price: event.payload.price, size: event.payload.size });
        while state.trades.front().is_some_and(|trade| trade.ts < event.source_ts - 300.0) { state.trades.pop_front(); }
        if event.source_ts - state.last_eval < 1.0 { return None; }
        state.last_eval = event.source_ts;
        let evaluation = evaluate(&event.symbol, state);
        let qualified = evaluation.candidate.is_some();
        let (emit, armed_after) = update_armed_state(state.armed, qualified, evaluation.continuity_holds);
        state.armed = armed_after;
        if emit { evaluation.candidate } else { None }
    }
}

pub fn validate_event(event: &MarketEvent) -> Result<(), String> {
    if event.schema != SCHEMA_VERSION
        || event.event_type != "trade"
        || event.source_ts <= 0.0
        || event.payload.price <= 0.0
        || event.payload.size < 0.0
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
    fn sparse_history_warms_by_closed_buckets_not_trade_count() {
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
                payload: Payload { price: 1.0, size: 100.0 },
            });
        }
        let report = run(events, Integrity::default());
        assert!(
            !report.candidates.is_empty(),
            "8 closed 15-second buckets should satisfy warmup even with fewer than 12 trades"
        );
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
            payload: Payload { price: 1.0, size: 1.0 },
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
                payload: Payload { price: 1.0, size: 100.0 },
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