use scout_market_replay::{load_events, parse_event_line, run, Engine};
use std::env;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

fn stream_mode() -> Result<(), String> {
    let stdin = io::stdin();
    let stdout_handle = io::stdout();
    let mut stdout = io::BufWriter::new(stdout_handle.lock());
    let mut engine = Engine::default();
    for (index, line) in stdin.lock().lines().enumerate() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let event = parse_event_line(&line)
            .map_err(|error| format!("invalid stream event at line {}: {}", index + 1, error))?;
        if let Some(candidate) = engine.process_event(event) {
            serde_json::to_writer(&mut stdout, &candidate).map_err(|error| error.to_string())?;
            stdout.write_all(b"\n").map_err(|error| error.to_string())?;
            stdout.flush().map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn replay_mode(args: &[String]) -> Result<(), String> {
    if args.len() != 4 || args[2] != "--output" {
        return Err("usage: scout-market-replay <dataset.ndjson> --output <report.json> | --stream".to_string());
    }
    let input = PathBuf::from(&args[1]);
    let output = PathBuf::from(&args[3]);
    let (events, integrity) = load_events(&input)?;
    let report = run(events, integrity);
    let body = serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?;
    fs::write(output, body).map_err(|error| error.to_string())?;
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let result = if args.len() == 2 && args[1] == "--stream" {
        stream_mode()
    } else {
        replay_mode(&args)
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
