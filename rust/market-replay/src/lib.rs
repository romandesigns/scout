use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

pub const SCHEMA_VERSION: &str = "scout.market-event.v1";

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
        let event: MarketEvent = serde_json::from_str(&line).map_err(|error| {
            integrity.malformed += 1;
            format!("invalid event at line {}: {}", index + 1, error)
        })?;
        if event.schema != SCHEMA_VERSION || event.event_type != "trade" || event.source_ts <= 0.0 || event.payload.price <= 0.0 || event.payload.size < 0.0 {
            integrity.malformed += 1;
            return Err(format!("invalid event contract at line {}", index + 1));
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

fn evaluate(symbol: &str, window: &SymbolWindow) -> Option<Candidate> {
    let latest = window.trades.back()?;
    if window.trades.len() < 12 { return None; }
    let base_cutoff = latest.ts - 300.0;
    let trigger_cutoff = latest.ts - 5.0;
    let base: Vec<&Trade> = window.trades.iter().filter(|trade| trade.ts >= base_cutoff).collect();
    let prior: Vec<&Trade> = base.iter().copied().filter(|trade| trade.ts < trigger_cutoff).collect();
    if prior.len() < 8 { return None; }
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
    if score < 7 || extension > 0.75 || !(-0.35..=0.75).contains(&trigger_distance) { return None; }
    Some(Candidate { ticker: symbol.to_string(), detected_at: latest.ts, price: latest.price, stage: "PRE_IGNITION", lifecycle_phase: "ARMED", shadow_mode: true, recipe_score: score, recipe_present: present, recipe_missing: missing, trigger_distance_pct: trigger_distance, base_extension_pct: extension })
}

pub fn run(events: Vec<MarketEvent>, integrity: Integrity) -> ReplayReport {
    let processed_events = events.len();
    let mut states: HashMap<String, SymbolWindow> = HashMap::new();
    let mut candidates = Vec::new();
    for event in events {
        let state = states.entry(event.symbol.clone()).or_default();
        state.trades.push_back(Trade { ts: event.source_ts, price: event.payload.price, size: event.payload.size });
        while state.trades.front().is_some_and(|trade| trade.ts < event.source_ts - 300.0) { state.trades.pop_front(); }
        if event.source_ts - state.last_eval < 1.0 { continue; }
        state.last_eval = event.source_ts;
        if let Some(candidate) = evaluate(&event.symbol, state) {
            if !state.armed { candidates.push(candidate); state.armed = true; }
        } else { state.armed = false; }
    }
    ReplayReport { engine: "scout-market-replay-rust/0.1.0", mode: "SIMULATION", schema_version: SCHEMA_VERSION, processed_events, integrity, candidates }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pct_is_directional() { assert!((pct(10.0, 10.2) - 2.0).abs() < 0.000001); }
    #[test]
    fn empty_replay_is_isolated() {
        let report = run(Vec::new(), Integrity::default());
        assert_eq!(report.mode, "SIMULATION");
        assert_eq!(report.processed_events, 0);
    }
}
