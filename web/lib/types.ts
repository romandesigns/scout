export type Finding = {
  id: number;
  ticker: string;
  stage: string;
  detected_at: number;
  price: number;
  score: number;
  vol_ratio_15s: number | null;
  vol_ratio_30s: number | null;
  change_60s_pct: number | null;
  extension_pct: number | null;
  ema9: number | null;
  ema21: number | null;
  ema9_slope: number | null;
  vwap: number | null;
  above_vwap: boolean;
  quiet_break: boolean;
  evidence: string[];
  catalyst_headline?: string | null;
  catalyst_category?: string | null;
  catalyst_score?: number | null;
  catalyst_url?: string | null;
  chart_url?: string | null;
  change_3s_pct?: number | null;
  change_5s_pct?: number | null;
  change_10s_pct?: number | null;
  change_15s_pct?: number | null;
  change_30s_pct?: number | null;
  accel_15s_pp?: number | null;
  dollar_volume_15s?: number | null;
  dollar_volume_30s?: number | null;
  trades_15s?: number | null;
  trades_30s?: number | null;
  breakout_level?: number | null;
  breakout_window?: string | null;
  signals?: string[];
  quality_label?: "CLEAN" | "DEVELOPING" | "CHOPPY" | "ILLIQUID";
  quality_score?: number;
  actionable_rank?: "A" | "B" | "C";
  rejection_reasons?: string[];
  directional_efficiency?: number | null;
  active_bucket_ratio?: number | null;
  direction_reversals?: number | null;
  previous_close?: number | null;
  gap_pct?: number | null;
  day_volume?: number | null;
  projected_session_volume?: number | null;
  volume_rate_per_minute?: number | null;
  float_shares?: number | null;
  float_turnover?: number | null;
  candidate_profile?: { velocity?:number|null; participation?:number|null; structure?:number|null; catalyst?:number|null; quality?:number|null; supply?:number|null; multi_timeframe?: { qualified?:boolean; gates?:Record<string,boolean>; blockers?:string[]; five_minute_samples?:number; five_minute_change_pct?:number; one_minute_change_pct?:number; one_minute_higher_low_ratio?:number; change_30s_pct?:number; fast_tape_veto?:boolean }; decision_chart?: { primary_seconds?:15|30|60|300; trigger_seconds?:15|30|60|300; context_seconds?:15|30|60|300; instruction?:string }; promotion_trace?: { gates?: Record<string, boolean>; blockers?: string[]; next_blocker?: string | null }; edge_validation?: { status?:"PROFIT_VALIDATED"|"EVALUATING"; validated?:boolean; cohort?:string; samples?:number; minimum_samples?:number; wins?:number; win_rate?:number|null; wilson_lower?:number|null; break_even_rate?:number; average_r?:number|null } };
  episode_id?: number;
  reversal_phase?: string | null;
  reversal_low?: number | null;
  reversal_drawdown_pct?: number | null;
  leg_context?: string | null;
  ross_match?: boolean;
  ross_score?: number;
  detection_timeframe_seconds?: number;
  formation_start_at?: number | null;
  formation_end_at?: number | null;
  formation_low?: number | null;
  formation_high?: number | null;
  trigger_level?: number | null;
  invalidation_level?: number | null;
  halt_pressure_score?: number;
  urgency?: "NOW" | "EARLY" | "WATCH" | "CONFIRMED" | "EXTENDED" | "RISK";
  engine_version?: string | null;
  lifecycle_phase?: "DEVELOPING"|"ARMED"|"AWAKENING"|"IGNITING"|"CONFIRMED"|"REARM"|null;
  shadow_mode?: boolean;
  recipe_score?: number;
  recipe_present?: string[];
  recipe_missing?: string[];
  trigger_distance_pct?: number|null;
  base_extension_at_detection_pct?: number|null;
  timeliness_label?: "PRE_IGNITION"|"AT_IGNITION"|"LATE"|null;
  precursor_finding_id?: number|null;
  engine_source?: "rust"|"python"|string;
  hybrid_sources?: string[];
  hybrid_score?: number;
  hybrid_key?: string|null;
  notification_reason?: string|null;
  notification_delivered_at?: number|null;
  opportunity_class?: "FIRST_MOVE"|"SECONDARY_ENTRY"|"LATE_INFORMATION_ONLY"|"EVENT"|null;
  selection_context?: "finding"|"catalyst"|"gainer"|"halt"|"validation";
  selection_title?: string;
  selection_detail?: string;
};

export type DeliveryEvent = { id:number; finding_id:number; channel:string; status:string; event_at:number; detail?:string|null; provider_id?:string|null };
export type PipelineTraceEvent = { id:number; finding_id:number; stage:string; event_at:number; channel?:string|null; detail?:string|null };
export type FindingVerification = {
  finding:Finding;
  outcome:{max_1m_pct:number|null;max_5m_pct:number|null;max_15m_pct:number|null;max_session_pct:number|null;time_to_peak_seconds:number|null;updated_at:number|null}|null;
  automatic_grade:number;
  automatic_label:string;
  grade_reasons:string[];
  delivery:DeliveryEvent[];
  pipeline_trace?:PipelineTraceEvent[];
  legacy_delivery_audit:boolean;
  review?:{user_grade?:number|null;user_agrees?:boolean|null;reason_tags?:string[];notes?:string;reviewed_at?:number|null}|null;
};

export type Catalyst = {
  id: number;
  ticker: string;
  headline: string;
  category: string;
  score: number;
  url: string;
  source: string;
  published_at: number;
};

export type Gainer = {
  symbol: string;
  price?: number;
  change?: number;
  percent_change?: number;
  scout?: { id: number; ticker?: string; stage: string; detected_at: number; price: number; score: number; signals?: string[] };
};

export type Halt = {
  id?: number;
  ticker: string;
  status_code: string;
  status_message: string;
  reason_code: string;
  reason_message: string;
  event_at: number;
  is_halted: boolean;
};

export type Bucket = {
  start_ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trades: number;
};

export type MarketMetrics = {
  price?: number;
  vol15?: number;
  vol30?: number;
  dollar15?: number;
  dollar30?: number;
  trades15?: number;
  trades30?: number;
  change3?: number;
  change5?: number;
  change10?: number;
  change15?: number;
  change30?: number;
  change60?: number;
  accel15_pp?: number | null;
  ema9?: number | null;
  ema21?: number | null;
  vwap?: number | null;
  above_vwap?: boolean;
  quiet_break?: boolean;
  staircase?: boolean;
  stair_change?: number;
  stair_up_ratio?: number;
  stair_higher_low_ratio?: number;
  surge?: boolean;
  breakout?: boolean;
  breakout_level?: number | null;
  breakout_window?: string | null;
  breakout_penetration_pct?: number;
  resistance_levels?: Record<string, number>;
  extension?: number;
  evidence?: string[];
  quality_label?: string;
  quality_score?: number;
  actionable_rank?: string;
  rejection_reasons?: string[];
  directional_efficiency?: number;
  active_bucket_ratio?: number;
  direction_reversals?: number;
  latest_trade_age?: number;
  max_gap_pct?: number;
  median_wick_ratio?: number;
  bullish_confirmed?: boolean;
  previous_close?: number | null;
  gap_pct?: number | null;
  day_volume?: number;
  projected_session_volume?: number;
  volume_rate_per_minute?: number;
  candidate_profile?: Record<string,number|null>;
  first_leg_watch?: boolean;
  first_leg_release?: boolean;
  leg_context?: string;
  ross_match?: boolean;
  ross_score?: number;
};

export type MarketSnapshot = {
  ticker: string;
  session_date: string;
  session_first_price?: number | null;
  buckets: Bucket[];
  metrics: MarketMetrics;
  halt?: Halt | null;
  findings: Finding[];
  catalysts: Catalyst[];
  statuses: Halt[];
  source?: "live" | "historical-trades" | "historical-bars";
  as_of?: number;
  historical_complete?: boolean;
  historical_pages?: number;
  historical_trade_count?: number;
  delivery?: DeliveryEvent[];
};

export type Diagnostic = {
  ticker: string;
  available: boolean;
  metrics?: MarketMetrics;
  gates?: Record<string, boolean>;
  reasons?: string[];
};

export type ValidationRow = {
  id: number;
  ticker: string;
  stage: string;
  detected_at: number;
  price: number;
  move_at_detection_pct: number | null;
  score: number;
  signals: string[];
  max_1m_pct: number | null;
  max_5m_pct: number | null;
  max_15m_pct: number | null;
  max_session_pct: number | null;
  time_to_peak_seconds: number | null;
  updated_at: number | null;
};

export type TimelineItem = {
  type: "finding" | "catalyst" | "halt" | "resume";
  at: number;
  ticker: string;
  payload: Finding | Catalyst | Halt;
};

export type ScoutStatus = {
  ok: boolean;
  app: string;
  version?: string;
  environment: string;
  feeds: { sip: boolean; boats: boolean | null; news: boolean; health?:Record<string,unknown> };
  universe: number;
  sip_subscribed: number;
  overnight_subscribed: number;
  tracked_states: number;
  active_halts: number;
  price_range: { min: number; max: number };
  market_quality?: { profile:string; min_active_ratio:number; min_trades_30s:number; min_dollar_30s:number; min_directional_efficiency:number };
  notifications: {
    master_enabled: boolean;
    android_enabled: boolean;
    windows_enabled: boolean;
    email_enabled: boolean;
    android_delivery_configured?: boolean;
    webpush_configured?: boolean;
    webpush_subscriptions?: number;
    email_delivery_configured?: boolean;
    windows_delivery_available?: boolean;
    queues?: Record<string, unknown>;
    delivery?: Record<string, unknown>;
  };
  trader?: TraderSettings;
  hybrid?: {
    rust_bridge?: {enabled:boolean;running:boolean;queue_depth?:number;submitted?:number;dropped?:number;candidates?:number;restarts?:number;last_error?:string|null};
    precision?: {threshold_pct:number;completed_episodes:number;successful_episodes:number;precision:number|null;source_mix:Record<string,number>};
    notification_latency?: Record<string,{samples:number;median_seconds:number;p95_seconds:number;max_seconds:number}>;
    pipeline_latency?: Record<string,{samples:number;median:number;p95:number;max:number}>;
    architecture?: string;
  };
  engines: Record<string, boolean>;
  catalyst_sources?: {
    news_connected: boolean | null;
    last_news_at: number | null;
    last_sec_ok_at: number | null;
    last_rss_ok_at: number | null;
    rss_configured: boolean;
    watchlist_size?: number;
    source_stale_seconds?: number;
    health?: Record<string,{last_ok_at:number|null;last_error:string|null}>;
  };
  replay?: {
    mode:"LIVE"|"SIMULATION";
    active:boolean;
    latest_run:null|{
      run_id:string; status:string; dataset:string; processed_events:number;
      findings_count:number; schema_version:string; scout_version:string;
      completed_at:number; benchmark?:{events_per_second:number;peak_memory_bytes:number};
      calibration?:{precursors:number;successful_precursors:number;false_arms:number;false_arm_rate:number|null;median_lead_seconds:number|null;expansion_episodes:number;missed_expansions:number};
    };
  };
};

export type PushConfig = { enabled:boolean; public_key:string; subscriptions:number };

export type NotificationPreferences = {
  master_enabled: boolean;
  platforms: {
    android: { enabled: boolean; sound: boolean; vibration: boolean; priority: string };
    windows: { enabled: boolean; sound: boolean; toast: boolean; priority: string };
    email: { enabled: boolean };
  };
  signals: Record<string, "notify" | "silent" | "off">;
  sessions: Record<string, boolean>;
  quiet_hours: { enabled: boolean; start: string; end: string; allow_critical: boolean };
  minimum_score: number;
  only_stage_escalations: boolean;
  group_by_ticker: boolean;
  market_quality_profile: "strict" | "balanced" | "permissive";
};

export type ScannerSettings = { min_price:number; max_price:number };

export type TraderSettings = {
  enabled:boolean; mode:"paper"; configured:boolean; paper_safe:boolean;
  risk_reward:number; position_notional:number; max_positions:number;
  daily_loss_limit:number; max_stop_pct:number; last_error?:string|null; last_order_at?:number|null;
  performance?:{total:number;open:number;closed:number;wins:number;win_rate:number|null;realized_pl:number};
};

export type PaperTrade = {
  id:number; episode_key:string; finding_id:number; ticker:string; client_order_id:string;
  alpaca_order_id?:string|null; status:string; quantity:number; signal_price:number;
  entry_price?:number|null; stop_price:number; target_price:number; exit_price?:number|null;
  submitted_at:number; filled_at?:number|null; closed_at?:number|null; exit_reason?:string|null; realized_pl?:number|null;
};

export type AttentionStatus = "unread"|"opened"|"watching"|"acknowledged"|"dismissed"|"expired";
export type AttentionItem = {
  id:number;
  episode_key:string;
  ticker:string;
  first_finding_id:number;
  latest_finding_id:number;
  priority:number;
  status:AttentionStatus;
  created_at:number;
  updated_at:number;
  finding:Finding;
};
