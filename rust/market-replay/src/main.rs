use scout_market_replay::{load_events, run};
use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 || args[2] != "--output" {
        eprintln!("usage: scout-market-replay <dataset.ndjson> --output <report.json>");
        std::process::exit(2);
    }
    let input = PathBuf::from(&args[1]);
    let output = PathBuf::from(&args[3]);
    let (events, integrity) = load_events(&input).unwrap_or_else(|error| { eprintln!("{error}"); std::process::exit(1) });
    let report = run(events, integrity);
    let body = serde_json::to_string_pretty(&report).expect("serialize report");
    fs::write(output, body).unwrap_or_else(|error| { eprintln!("{error}"); std::process::exit(1) });
}
