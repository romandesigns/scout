"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  IconActivity,
  IconAdjustmentsFilled,
  IconBellFilled,
  IconBolt,
  IconChartBar,
  IconChevronDown,
  IconChevronUp,
  IconDiamondFilled,
  IconDots,
  IconFlame,
  IconHistory,
  IconLayoutBottombarExpand,
  IconLayoutColumns,
  IconLayoutDashboard,
  IconLayoutGrid,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarRightCollapse,
  IconMaximize,
  IconMinimize,
  IconPlayerPauseFilled,
  IconPin,
  IconRefresh,
  IconSearch,
  IconSettings,
  IconTableFilled,
  IconTargetArrow,
  IconTrendingUp,
  IconX,
} from "@tabler/icons-react";
import {
  MdBolt, MdCheckCircle, MdDiamond, MdLocalFireDepartment, MdNewReleases,
  MdOutlinePauseCircleFilled, MdOutlineReplay, MdPlayCircleFilled,
  MdStairs, MdTrackChanges, MdTrendingUp, MdVisibility,
} from "react-icons/md";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ScoutTooltip, TooltipProvider } from "@/components/ui/tooltip";
import { LiveChart } from "@/components/live-chart";
import { PwaRuntime } from "@/components/pwa-runtime";
import { CandidateProfileChart, ParticipationChart, QualityGauge, TimeOfDayOutcomeChart, ValidationOutcomeChart, VelocityChart } from "@/components/scout-data-charts";
import {
  API_BASE,
  API_CONFIGURED,
  getCatalysts,
  getFinding,
  getFindings,
  getGainers,
  getHalts,
  getScannerSettings,
  getNotificationPreferences,
  getStatus,
  getTimeline,
  getValidation,
  getAttention,
  getFindingVerification,
  prefetchMarketSnapshot,
  saveFindingReview,
  updateAttention,
  saveNotificationPreferences,
  saveScannerSettings,
  testNotification,
} from "@/lib/api";
import { getNativeAutostartState, installNativeDeepLinkHandler, installNativeNotificationActionHandler, sendNativeTestNotification, setNativeAutostart } from "@/lib/native";
import { disableWebPush, enableWebPush, webPushState, type WebPushState } from "@/lib/web-push";
import type {
  Catalyst,
  Finding,
  Gainer,
  Halt,
  NotificationPreferences,
  ScoutStatus,
  ScannerSettings,
  TimelineItem,
  ValidationRow,
  AttentionItem,
  AttentionStatus,
  FindingVerification,
} from "@/lib/types";

type ActivityView = "radar" | "ross" | "catalysts" | "gainers" | "halts" | "validation" | "alerts" | "settings";
type MobileView = "radar" | "charts" | "catalysts" | "alerts" | "settings";
type MarketTab = "radar" | "ross" | "gainers" | "halted";
type DockTab = "catalysts" | "evidence" | "validation" | "events";
type GroupMode = 1 | 2 | 4;
type ChartGroupState = { id: string; ticker?: string; pinned?: boolean };

type UiPreferences = {
  compactDensity: boolean;
  showChartMarkers: boolean;
  showFormationRegion:boolean;
  showDetectionPrice:boolean;
  showTriggerLevel:boolean;
  showInvalidationLevel:boolean;
  autoCenterDetection:boolean;
  openDetectionTimeframe:boolean;
};

const UI_PREFS_KEY = "stockhunter-scout-ui-v1";
const DEFAULT_UI_PREFS: UiPreferences = { compactDensity:true,showChartMarkers:true,showFormationRegion:true,showDetectionPrice:true,showTriggerLevel:true,showInvalidationLevel:true,autoCenterDetection:true,openDetectionTimeframe:true };

function readUiPreferences(): UiPreferences {
  if (typeof window === "undefined") return DEFAULT_UI_PREFS;
  try {
    const raw = window.localStorage.getItem(UI_PREFS_KEY);
    if (!raw) return DEFAULT_UI_PREFS;
    const parsed = JSON.parse(raw) as Partial<UiPreferences>;
    return { ...DEFAULT_UI_PREFS, ...parsed };
  } catch {
    return DEFAULT_UI_PREFS;
  }
}

function applyUiPreferences(value: UiPreferences) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.scoutDensity = value.compactDensity ? "compact" : "comfortable";
  document.documentElement.dataset.scoutMarkers = value.showChartMarkers ? "on" : "off";
}

function persistUiPreferences(value: UiPreferences) {
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem(UI_PREFS_KEY, JSON.stringify(value)); } catch {}
  }
  applyUiPreferences(value);
}

type WorkbenchProps = {
  findings: Finding[];
  catalysts: Catalyst[];
  gainers: Gainer[];
  halts: Halt[];
  validation: ValidationRow[];
  timeline: TimelineItem[];
  status: ScoutStatus | null;
  connected: boolean;
  selected?: Finding;
  setSelected: (finding: Finding) => void;
  openNotifications: () => void;
  openCommand: () => void;
  scanner: ScannerSettings;
  saveScanner: (value:ScannerSettings)=>Promise<void>;
  scannerBusy:boolean;
  scannerMessage:string;
  attention:AttentionItem[];
  setAttentionStatus:(item:AttentionItem,status:AttentionStatus)=>Promise<void>;
};

const CLIENT_VERSION=process.env.NEXT_PUBLIC_SCOUT_VERSION||"dev";

const demoFindings: Finding[] = [
  {
    id: 1, ticker: "AKAN", stage: "BREAKOUT", detected_at: -18, price: 5.38, score: 11,
    vol_ratio_15s: 9.8, vol_ratio_30s: 7.3, change_60s_pct: 3.4, extension_pct: 4.2,
    ema9: 5.31, ema21: 5.22, ema9_slope: .03, vwap: 5.18, above_vwap: true, quiet_break: true,
    evidence: ["Abnormal participation", "5s velocity expanding", "Quiet range breakout", "EMA9 slope rising"],
    catalyst_headline: "Network expansion increases recurring revenue", catalyst_category: "Expansion", catalyst_score: 5,
    change_3s_pct: .44, change_5s_pct: .82, change_10s_pct: 1.21, change_15s_pct: 1.64, change_30s_pct: 2.31,
    accel_15s_pp: .47, dollar_volume_15s: 21400, dollar_volume_30s: 38400, trades_15s: 24, trades_30s: 42,
    breakout_level: 5.24, breakout_window: "3m", signals: ["EARLY", "SURGE", "BREAKOUT", "CATALYST"], chart_url: null,
  },
  {
    id: 2, ticker: "CAPR", stage: "EARLY", detected_at: -42, price: 4.14, score: 8,
    vol_ratio_15s: 6.4, vol_ratio_30s: 5.2, change_60s_pct: .8, extension_pct: -.7,
    ema9: 4.12, ema21: 4.10, ema9_slope: .01, vwap: 4.16, above_vwap: false, quiet_break: true,
    evidence: ["Participation expanding", "Range pressure", "EMA9 turning higher"],
    catalyst_headline: "Quarterly results and corporate update", catalyst_category: "Earnings", catalyst_score: 4,
    change_3s_pct: .08, change_5s_pct: .19, change_10s_pct: .32, change_15s_pct: .41, change_30s_pct: .73,
    accel_15s_pp: .22, dollar_volume_15s: 9300, dollar_volume_30s: 18100, trades_15s: 12, trades_30s: 23,
    signals: ["EARLY", "CATALYST"], chart_url: null,
  },
  {
    id: 3, ticker: "MGRX", stage: "IGNITION", detected_at: -74, price: .5769, score: 10,
    vol_ratio_15s: 11.4, vol_ratio_30s: 8.9, change_60s_pct: 4.9, extension_pct: 7.53,
    ema9: .574, ema21: .567, ema9_slope: .002, vwap: .565, above_vwap: true, quiet_break: true,
    evidence: ["Volume accelerating", "EMA9 > EMA21", "Price > VWAP"],
    change_3s_pct: .31, change_5s_pct: .61, change_10s_pct: .92, change_15s_pct: 1.38, change_30s_pct: 2.74,
    accel_15s_pp: .39, dollar_volume_15s: 18600, dollar_volume_30s: 35600, trades_15s: 31, trades_30s: 55,
    breakout_level: .563, breakout_window: "5m", signals: ["SURGE", "BREAKOUT", "IGNITION"], chart_url: null,
  },
  {
    id: 4, ticker: "ONFO", stage: "STAIRCASE", detected_at: -104, price: 2.39, score: 7,
    vol_ratio_15s: 3.1, vol_ratio_30s: 3.8, change_60s_pct: 1.4, extension_pct: 1.4,
    ema9: 2.37, ema21: 2.35, ema9_slope: .01, vwap: 2.36, above_vwap: true, quiet_break: false,
    evidence: ["Higher lows", "Staircase developing", "EMA9 slope rising"],
    change_3s_pct: .04, change_5s_pct: .11, change_10s_pct: .22, change_15s_pct: .28, change_30s_pct: .51,
    accel_15s_pp: .10, dollar_volume_15s: 7100, dollar_volume_30s: 13300, trades_15s: 9, trades_30s: 19,
    signals: ["STAIRCASE"], chart_url: null,
  },
];

const demoCatalysts: Catalyst[] = [
  { id: 1, ticker: "AKAN", headline: "Network expansion increases recurring revenue", category: "Expansion", score: 5, url: "", source: "Alpaca News", published_at: -32 },
  { id: 2, ticker: "CAPR", headline: "Quarterly results and corporate update", category: "Earnings", score: 4, url: "", source: "Company PR", published_at: -76 },
  { id: 3, ticker: "MDXH", headline: "Quarterly financial results released", category: "Earnings", score: 3, url: "", source: "Alpaca News", published_at: -184 },
];

const demoGainers: Gainer[] = [
  { symbol: "AKAN", price: 11.51, percent_change: 129.7, scout: { id: 1, stage: "BREAKOUT", detected_at: -1800, price: 5.38, score: 11, signals: ["SURGE", "BREAKOUT"] } },
  { symbol: "CAPR", price: 6.89, percent_change: 63.7, scout: { id: 2, stage: "EARLY", detected_at: -2100, price: 4.14, score: 8 } },
  { symbol: "ONFO", price: 3.23, percent_change: 39.8 },
  { symbol: "MGRX", price: .589, percent_change: 10.5, scout: { id: 3, stage: "IGNITION", detected_at: -900, price: .5769, score: 10 } },
];

const demoHalts: Halt[] = [
  { ticker: "TPCS", status_code: "H", status_message: "Halted", reason_code: "LUDP", reason_message: "Volatility pause", event_at: -88, is_halted: true },
];

const demoValidation: ValidationRow[] = [
  { id:1,ticker:"AKAN",stage:"BREAKOUT",detected_at:-1800,price:5.38,move_at_detection_pct:1.12,score:11,signals:["SURGE","BREAKOUT"],max_1m_pct:3.1,max_5m_pct:12.8,max_15m_pct:29.4,max_session_pct:113.1,time_to_peak_seconds:2100,updated_at:null },
  { id:2,ticker:"CAPR",stage:"EARLY",detected_at:-2100,price:4.14,move_at_detection_pct:-2.13,score:8,signals:["EARLY","CATALYST"],max_1m_pct:1.2,max_5m_pct:9.4,max_15m_pct:31.2,max_session_pct:90.8,time_to_peak_seconds:2600,updated_at:null },
  { id:3,ticker:"MGRX",stage:"IGNITION",detected_at:-900,price:.5769,move_at_detection_pct:7.53,score:10,signals:["SURGE","BREAKOUT","IGNITION"],max_1m_pct:2.0,max_5m_pct:11.8,max_15m_pct:24.6,max_session_pct:38.7,time_to_peak_seconds:1400,updated_at:null },
];

const demoTimeline: TimelineItem[] = [
  { type:"finding",at:-18,ticker:"AKAN",payload:demoFindings[0] },
  { type:"catalyst",at:-32,ticker:"AKAN",payload:demoCatalysts[0] },
  { type:"finding",at:-42,ticker:"CAPR",payload:demoFindings[1] },
  { type:"halt",at:-88,ticker:"TPCS",payload:demoHalts[0] },
];

const defaultPrefs: NotificationPreferences = {
  master_enabled: true,
  platforms: {
    android: { enabled: true, sound: true, vibration: true, priority: "high" },
    windows: { enabled: false, sound: true, toast: false, priority: "high" },
    email: { enabled: false },
  },
  signals: { ACTIVITY_WATCH:"silent", REVERSAL_WATCH:"silent", FIRST_LEG_WATCH:"silent", PRE_IGNITION:"silent", AWAKENING:"notify", FIRST_LEG:"notify", RECLAIM:"notify", EMA_RECLAIM:"notify", VWAP_RECLAIM:"notify", FIRST_PULLBACK:"silent", EARLY:"notify", SURGE:"notify", BREAKOUT:"notify", STAIRCASE:"notify", IGNITION:"notify", CATALYST_WATCH:"notify", CATALYST_ACTIVE:"notify", HALT:"notify", RESUME:"notify", REARM:"notify" },
  sessions: { overnight:true, premarket:true, regular:true, afterhours:true },
  quiet_hours: { enabled:false, start:"22:00", end:"06:00", allow_critical:true },
  minimum_score: 0,
  only_stage_escalations: true,
  group_by_ticker: true,
  market_quality_profile: "balanced",
};

function age(ts: number) {
  if (ts < 0) {
    const seconds = Math.abs(Math.round(ts));
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds/60)}m`;
    return `${Math.floor(seconds/3600)}h`;
  }
  const seconds = Math.max(0, Math.round(Date.now()/1000 - ts));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds/60)}m`;
  return `${Math.floor(seconds/3600)}h`;
}

function clock(ts:number) {
  if (ts < 0) return age(ts);
  try { return new Date(ts * 1000).toLocaleTimeString([], { hour:"2-digit", minute:"2-digit", second:"2-digit" }); }
  catch { return "—"; }
}

function money(v?: number | null) {
  if (v == null || Number.isNaN(v)) return "—";
  return v < 1 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

function compactMoney(v?: number | null) {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1_000_000) return `$${(v/1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v/1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function contextualFinding(value:{kind:NonNullable<Finding["selection_context"]>;ticker:string;title:string;detail:string;at:number;price?:number;stage?:string;id?:number}):Finding{
  return {id:value.id??-Math.abs([...value.ticker].reduce((sum,char)=>sum+char.charCodeAt(0),0)),ticker:value.ticker,stage:value.stage||value.kind.toUpperCase(),detected_at:value.at,price:value.price||0,score:0,vol_ratio_15s:null,vol_ratio_30s:null,change_60s_pct:null,extension_pct:null,ema9:null,ema21:null,ema9_slope:null,vwap:null,above_vwap:false,quiet_break:false,evidence:[value.detail],signals:[],quality_label:"DEVELOPING",selection_context:value.kind,selection_title:value.title,selection_detail:value.detail};
}

function pct(v?: number | null, digits=2) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function tone(stage: string) {
  if (stage === "CONFIRMED") return "blue";
  if (stage === "NOW") return "green";
  if (["EXTENDED","RISK"].includes(stage)) return "red";
  if (stage === "AWAKENING") return "blue";
  if (stage === "FIRST_LEG") return "green";
  if (stage === "FIRST_LEG_WATCH") return "blue";
  if (["IGNITION","SURGE","RECLAIM","EMA_RECLAIM","VWAP_RECLAIM","REARM"].includes(stage)) return "green";
  if (["EARLY","BREAKOUT","CATALYST","CATALYST_WATCH","RESUME"].includes(stage)) return "blue";
  if (stage === "CATALYST_ACTIVE") return "green";
  if (["HALT","FAILED"].includes(stage)) return "red";
  return "orange";
}

function stageGlyph(stage: string) {
  if (stage === "AWAKENING") return <MdVisibility/>;
  if (stage === "FIRST_LEG") return <IconBolt size={11}/>;
  if (stage === "IGNITION") return <IconFlame size={11}/>;
  if (["CATALYST","CATALYST_WATCH","CATALYST_ACTIVE"].includes(stage)) return <IconDiamondFilled size={10}/>;
  if (stage === "SURGE") return <IconBolt size={11}/>;
  if (["BREAKOUT","REVERSAL_WATCH","RECLAIM","EMA_RECLAIM","VWAP_RECLAIM","REARM"].includes(stage)) return <IconTrendingUp size={11}/>;
  if (stage === "HALT") return <IconPlayerPauseFilled size={10}/>;
  return <IconTargetArrow size={11}/>;
}

const EVENT_HELP:Record<string,string>={
  CONFIRMED:"Confirmed — evidence and structure passed Scout's confirmation gates",
  WATCH:"Watch — developing setup that needs more confirmation",
  NOW:"Act now — highest-priority fresh event",
  EXTENDED:"Extended — price has moved materially beyond the detection area",
  RISK:"Risk — conditions require additional caution",
  EARLY:"Early — initial bullish activity before full ignition",
  IGNITION:"Ignition — bullish price and participation expansion",
  SURGE:"Surge — rapid short-window price acceleration",
  BREAKOUT:"Breakout — price cleared established resistance",
  REARM:"Re-arm — a qualified continuation opportunity after pullback",
  STAIRCASE:"Staircase — orderly higher-step progression",
  EMA_RECLAIM:"EMA reclaim — price recovered the exponential moving average",
  VWAP_RECLAIM:"VWAP reclaim — price recovered session VWAP",
  RECLAIM:"Reclaim — price recovered a key structural level",
  FIRST_LEG:"First leg — confirmed initial expansion from a base",
  FIRST_LEG_WATCH:"First-leg watch — base is developing before release",
  PRE_IGNITION:"Pre-ignition shadow — recipe is armed before release; this observation remains silent",
  AWAKENING:"Awakening — Rust primary perception detected a clean dormant-to-active transition",
  CATALYST:"Catalyst — fresh potentially bullish news",
  CATALYST_WATCH:"Catalyst watch — news exists but market reaction is unconfirmed",
  CATALYST_ACTIVE:"Active catalyst — news and market reaction are aligned",
  HALT:"Halt — exchange trading pause is active",
  HALT_PRESSURE:"Halt pressure — acceleration resembles pre-halt conditions; not a prediction",
  RESUME:"Resume — trading resumed after a halt",
  REVERSAL_WATCH:"Reversal watch — recovery is developing after a selloff",
};

function eventIcon(stage:string){
  if(stage==="CONFIRMED")return <MdCheckCircle/>;
  if(stage==="NOW"||stage==="FIRST_LEG")return <MdNewReleases/>;
  if(stage==="WATCH"||stage.endsWith("_WATCH"))return <MdVisibility/>;
  if(stage==="PRE_IGNITION"||stage==="ARMED")return <MdTrackChanges/>;
  if(stage==="AWAKENING")return <MdVisibility/>;
  if(stage==="IGNITION")return <MdLocalFireDepartment/>;
  if(stage==="SURGE"||stage==="HALT_PRESSURE")return <MdBolt/>;
  if(stage==="EARLY")return <MdTrackChanges/>;
  if(stage==="STAIRCASE")return <MdStairs/>;
  if(stage==="REARM")return <MdOutlineReplay/>;
  if(stage==="HALT")return <MdOutlinePauseCircleFilled/>;
  if(stage==="RESUME")return <MdPlayCircleFilled/>;
  if(stage.includes("CATALYST"))return <MdDiamond/>;
  return <MdTrendingUp/>;
}

function EventIcon({event}: {event:string}){
  const label=EVENT_HELP[event]||event.replaceAll("_"," ").toLowerCase();
  return <ScoutTooltip content={label}><span className="event-icon" data-tone={tone(event)} aria-label={label} role="img" tabIndex={0}>{eventIcon(event)}</span></ScoutTooltip>;
}

function eventGlyph(type:TimelineItem["type"], payload:TimelineItem["payload"]) {
  if (type === "catalyst") return <IconDiamondFilled size={10}/>;
  if (type === "halt") return <IconPlayerPauseFilled size={10}/>;
  if (type === "resume") return <IconTrendingUp size={11}/>;
  const stage = (payload as Finding).stage;
  return stageGlyph(stage);
}

function IconButton({ label, active, onClick, children }: { label: string; active?: boolean; onClick?: () => void; children: React.ReactNode }) {
  return <ScoutTooltip content={label}><button type="button" aria-label={label} data-active={active || undefined} onClick={onClick} className="icon-button">{children}</button></ScoutTooltip>;
}

function PanelTitle({ icon, title, subtitle, actions }: { icon?: React.ReactNode; title: string; subtitle?: string; actions?: React.ReactNode }) {
  return <div className="pane-titlebar">
    <div className="pane-title"><span className="pane-title-icon">{icon}</span><span>{title}</span>{subtitle && <span className="pane-subtitle">{subtitle}</span>}</div>
    <div className="pane-actions">{actions}</div>
  </div>;
}

function FindingRow({ finding, selected, onSelect }: { finding: Finding; selected: boolean; onSelect: () => void }) {
  const [menu,setMenu]=useState<{x:number;y:number}|null>(null);
  const signals = Array.from(new Set([finding.stage, ...(finding.signals || [])])).slice(0, 3);
  const quality=finding.quality_label || (finding.stage==="ACTIVITY_WATCH"?"DEVELOPING":"CLEAN");
  const qualityTone=quality==="CLEAN"?"green":quality==="DEVELOPING"?"blue":quality==="CHOPPY"?"orange":"red";
  const urgency=finding.urgency||(finding.extension_pct!=null&&finding.extension_pct>=8?"EXTENDED":finding.stage==="FIRST_LEG"?"NOW":finding.quality_label==="CLEAN"?"CONFIRMED":"WATCH");
  const priority=urgency==="NOW"||finding.stage==="HALT_PRESSURE";
  return <><button className="market-row" data-selected={selected || undefined} data-priority={priority||undefined} onClick={onSelect} onContextMenu={event=>{event.preventDefault();setMenu({x:event.clientX,y:event.clientY});}}>
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="ticker-symbol">{finding.ticker}</span>
        <EventIcon event={urgency}/>
        {signals.map(signal => <EventIcon key={signal} event={signal}/>)}
        <Badge data-tone={qualityTone}>{finding.actionable_rank || "C"} · {quality}</Badge>
        {finding.catalyst_headline && !signals.includes("CATALYST") && <EventIcon event="CATALYST"/>}
        {finding.ross_match && <Badge data-tone="green">ROSS {finding.ross_score ?? ""}</Badge>}
      </div>
      <span className="scout-muted metric text-[10px]">{age(finding.detected_at)}</span>
    </div>
    <div className="mt-1 flex items-end justify-between gap-3">
      <div><span className="metric text-[14px] font-semibold">{money(finding.price)}</span><span className="ml-2 metric text-[10px] text-[var(--green)]">{pct(finding.extension_pct)}</span></div>
      <span className="metric text-[10px] scout-muted">evidence {finding.score}/10</span>
    </div>
    <div className="market-metrics mt-1.5">
      <span><b>5s</b> {pct(finding.change_5s_pct,1)}</span>
      <span><b>15s</b> {pct(finding.change_15s_pct,1)}</span>
      <span><b>30s</b> {pct(finding.change_30s_pct,1)}</span>
      <span><b>RV15</b> {finding.vol_ratio_15s?.toFixed(1) ?? "—"}×</span>
    </div>
    {finding.leg_context && <div className="mt-1 text-[9px] font-semibold tracking-wide text-[var(--green)]">{finding.leg_context.replaceAll("_"," ")} · {age(finding.detected_at)} AGO</div>}
    {finding.rejection_reasons?.length ? <div className="mt-1 truncate text-[9px] text-[var(--orange)]">{finding.rejection_reasons.slice(0,2).join(" · ")}</div> : null}
  </button>{menu&&<div className="stock-context-menu" style={{left:menu.x,top:menu.y}} onMouseLeave={()=>setMenu(null)}><Button variant="ghost" onClick={()=>{onSelect();setMenu(null);}}>Open in active chart</Button><Button variant="ghost" onClick={()=>{onSelect();setMenu(null);}}>Inspect event</Button><Button variant="ghost" onClick={()=>{void navigator.clipboard.writeText(finding.ticker);setMenu(null);}}>Copy ticker</Button><Button variant="ghost" onClick={()=>{void navigator.clipboard.writeText(`${finding.ticker} ${finding.stage} ${money(finding.price)} ${clock(finding.detected_at)}`);setMenu(null);}}>Copy alert summary</Button><Button variant="ghost" onClick={()=>{window.open(`https://www.tradingview.com/symbols/${finding.ticker}/`,`_blank`,`noopener,noreferrer`);setMenu(null);}}>Open external chart</Button></div>}</>;
}

function GainerRows({ gainers, findings, onSelect }: { gainers:Gainer[]; findings:Finding[]; onSelect:(finding:Finding)=>void }) {
  const findingByTicker = useMemo(() => new Map(findings.map(f => [f.ticker, f])), [findings]);
  return <>{gainers.map((g,i) => {
    const scout = findingByTicker.get(g.symbol) ?? findings.find(f => f.id === g.scout?.id);
    const lead = g.percent_change != null && scout?.extension_pct != null ? g.percent_change - scout.extension_pct : null;
    const selection=scout??contextualFinding({kind:"gainer",ticker:g.symbol,title:`Top gainer · ${g.symbol}`,detail:"No Scout detection is linked to this market-gainer row.",at:Date.now()/1000,price:g.price,stage:"MARKET_GAINER"});
    return <button key={g.symbol} className="market-row" data-selected={undefined} onClick={()=>onSelect(selection)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2"><span className="rank">{i+1}</span><b>{g.symbol}</b>{scout && <Badge data-tone="blue">SCOUT</Badge>}</div>
        <b className="metric text-[var(--green)]">{pct(g.percent_change)}</b>
      </div>
      <div className="mt-1 flex justify-between text-[10px]">
        <span className="scout-muted">{scout ? `${scout.stage} @ ${money(scout.price)}${lead != null ? ` · lead ${lead.toFixed(1)}pp` : ""}` : "Not detected"}</span>
        <span className="metric">{money(g.price)}</span>
      </div>
    </button>;
  })}</>;
}

function HaltRows({ halts, findings, onSelect }: { halts:Halt[]; findings:Finding[]; onSelect:(finding:Finding)=>void }) {
  if (!halts.length) return <EmptyPane text="No active halts"/>;
  return <>{halts.map(h => {
    const preHalt = findings.find(f => f.ticker === h.ticker && f.detected_at <= h.event_at && !["HALT","RESUME"].includes(f.stage));
    const current = findings.find(f => f.ticker === h.ticker);
    const scout = preHalt ?? current;
    const selection=scout??contextualFinding({kind:"halt",ticker:h.ticker,title:`Active halt · ${h.ticker}`,detail:`${h.reason_code||h.status_code} · ${h.reason_message||h.status_message}`,at:h.event_at,stage:"HALT"});
    return <button key={`${h.ticker}-${h.event_at}`} className="market-row" onClick={()=>onSelect(selection)}>
      <div className="flex justify-between gap-2"><div className="flex items-center gap-2"><b>{h.ticker}</b><EventIcon event="HALT"/></div><span className="text-[10px] scout-muted">{age(h.event_at)}</span></div>
      <div className="mt-1.5 flex justify-between gap-3 text-[10px] scout-muted"><span className="truncate">{h.reason_code || h.status_code} · {h.reason_message || h.status_message}</span><span className="shrink-0">{preHalt ? `${preHalt.stage} @ ${money(preHalt.price)}` : scout ? `Scout ${scout.stage}` : "No pre-halt signal"}</span></div>
    </button>;
  })}</>;
}

function MarketPulse({ findings, gainers, halts, selectedId, onSelect }: { findings: Finding[]; gainers: Gainer[]; halts: Halt[]; selectedId?: number; onSelect: (f: Finding)=>void }) {
  const [scope,setScope]=useState<"actionable"|"developing"|"all">("actionable");

  // Radar is a live decision surface, not historical storage. Old persisted
  // lifecycle records remain available in Inspector/history, but they should
  // not outrank fresh opportunities in the active radar.
  const radarBuckets=useMemo(()=>{
    const now=Date.now()/1000;
    const developingStages=new Set(["ACTIVITY_WATCH","PRE_IGNITION","AWAKENING","FIRST_LEG_WATCH","REVERSAL_WATCH","CATALYST_WATCH"]);
    const isDeveloping=(f:Finding)=>Boolean(f.shadow_mode)||developingStages.has(f.stage)||f.quality_label!=="CLEAN";
    const rankValue=(value?:string)=>value==="A"?3:value==="B"?2:1;
    const stageValue=(f:Finding)=>{
      const weights:Record<string,number>={IGNITION:8,BREAKOUT:7,SURGE:6,EARLY:5,FIRST_LEG:4,AWAKENING:3,PRE_IGNITION:2,ACTIVITY_WATCH:1};
      return weights[f.stage]??0;
    };
    const freshness=(f:Finding)=>Math.max(0,now-(f.detected_at||0));
    const withinLiveWindow=(f:Finding,developing:boolean)=>freshness(f) <= (developing ? 2*60*60 : 45*60);
    const sortRows=(rows:Finding[])=>rows.slice().sort((a,b)=>{
      const priorityDelta=rankValue(b.actionable_rank)-rankValue(a.actionable_rank);
      if(priorityDelta) return priorityDelta;
      const stageDelta=stageValue(b)-stageValue(a);
      if(stageDelta) return stageDelta;
      const qualityDelta=(b.quality_score||0)-(a.quality_score||0);
      if(qualityDelta) return qualityDelta;
      const evidenceDelta=(b.score||0)-(a.score||0);
      if(evidenceDelta) return evidenceDelta;
      // For otherwise comparable rows, freshest detection wins.
      return (b.detected_at||0)-(a.detected_at||0);
    });
    const actionable=sortRows(findings.filter(f=>!isDeveloping(f)&&withinLiveWindow(f,false)));
    const developing=sortRows(findings.filter(f=>isDeveloping(f)&&withinLiveWindow(f,true)));
    const all=sortRows([...actionable,...developing]);
    return {actionable,developing,all};
  },[findings]);

  const visibleFindings=radarBuckets[scope];
  return <div className="flex h-full min-h-0 flex-col">
    <PanelTitle icon={<IconActivity size={14}/>} title="RADAR" actions={<IconButton label="Radar filters"><IconAdjustmentsFilled size={14}/></IconButton>}/>
    <div className="radar-scope">{(["actionable","developing","all"] as const).map(value=><button key={value} data-active={scope===value||undefined} onClick={()=>setScope(value)}><span>{value}</span><span className="radar-scope-count" aria-label={`${radarBuckets[value].length} ${value}`}>{radarBuckets[value].length}</span></button>)}</div>
    <div className="min-h-0 flex-1 overflow-y-auto">
      {visibleFindings.length ? visibleFindings.map(f => <FindingRow key={f.ticker} finding={f} selected={selectedId===f.id} onSelect={()=>onSelect(f)}/>) : <EmptyPane text={scope==="actionable"?"No fresh actionable setups":"No fresh candidates in this view"}/>}
    </div>
  </div>;
}

function CatalystList({ catalysts, findings=[], onSelect }: { catalysts: Catalyst[]; findings?:Finding[]; onSelect?: (finding:Finding)=>void }) {
  const groups=useMemo(()=>{
    const byStory=new Map<string,{primary:Catalyst;tickers:string[]}>();
    for(const catalyst of catalysts){
      const key=catalyst.headline.trim().toLowerCase().replace(/\s+/g," ");
      const group=byStory.get(key);
      if(group){if(!group.tickers.includes(catalyst.ticker))group.tickers.push(catalyst.ticker);}
      else byStory.set(key,{primary:catalyst,tickers:[catalyst.ticker]});
    }
    return Array.from(byStory.values());
  },[catalysts]);
  return <div className="min-h-0 overflow-y-auto">
    {groups.length===0 ? <EmptyPane text="No recent catalysts"/> : groups.map(({primary:c,tickers})=>{const related=findings.find(f=>tickers.includes(f.ticker));const selection=related??contextualFinding({kind:"catalyst",ticker:c.ticker,title:c.headline,detail:`${c.category} · ${c.source}`,at:c.published_at,stage:"CATALYST_WATCH",id:-c.id});return <div key={c.id} className="event-row" role="button" tabIndex={0} data-selected={selection.id===related?.id||undefined} onClick={()=>onSelect?.(selection)} onKeyDown={event=>{if(event.key==="Enter"||event.key===" ")onSelect?.(selection)}}>
      <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><IconDiamondFilled size={10} className="text-[var(--cyan)]"/><Badge data-tone={c.score>=4?"green":"blue"}>{c.category || "Bullish"}</Badge></div><span className="text-[10px] scout-muted">{age(c.published_at)}</span></div>
      <div className="mt-1 line-clamp-2 text-[11px] leading-relaxed">{c.headline}</div>
      <div className="mt-1 flex items-center gap-1"><span className="mr-1 text-[10px] scout-muted">{c.source}</span>{tickers.slice(0,6).map(ticker=><button key={ticker} className="catalyst-ticker" onClick={event=>{event.stopPropagation();const match=findings.find(f=>f.ticker===ticker);onSelect?.(match??contextualFinding({kind:"catalyst",ticker,title:c.headline,detail:`${c.category} · ${c.source}`,at:c.published_at,stage:"CATALYST_WATCH",id:-c.id}))}}>{ticker}</button>)}{tickers.length>6&&<span className="text-[9px] scout-muted">+{tickers.length-6}</span>}</div>
    </div>})}
  </div>;
}

function gradeValidation(row:ValidationRow) {
  const move = row.move_at_detection_pct;
  if (move == null) return {label:"TRACKING", tone:"blue"};
  if (move <= 3) return {label:"EARLY", tone:"green"};
  if (move <= 6) return {label:"TIMELY", tone:"green"};
  if (move <= 10) return {label:"LATE", tone:"orange"};
  return {label:"TOO LATE", tone:"red"};
}

function ValidationPanel({ rows, findings, selectedId, onSelect }: { rows:ValidationRow[];findings:Finding[];selectedId?:number;onSelect:(finding:Finding)=>void }) {
  return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconHistory size={14}/>} title="VALIDATION" subtitle={`${rows.length} detections`}/><div className="min-h-0 flex-1 overflow-y-auto">{rows.length ? <><div className="validation-chart-card"><ValidationOutcomeChart rows={rows}/></div><div className="validation-chart-card"><div className="chart-card-label">SIGNALS &amp; 5-MIN FOLLOW-THROUGH BY TIME</div><TimeOfDayOutcomeChart rows={rows}/></div>{rows.map(row=>{
    const grade=gradeValidation(row);
    const finding=findings.find(f=>f.id===row.id)||findings.find(f=>f.ticker===row.ticker&&f.detected_at===row.detected_at)||contextualFinding({kind:"validation",ticker:row.ticker,title:`Validation · ${row.ticker}`,detail:`${grade.label} outcome review`,at:row.detected_at,price:row.price,stage:row.stage,id:row.id});
    return <button key={row.id} className="market-row" data-selected={selectedId===finding.id||undefined} onClick={()=>onSelect(finding)}><div className="flex justify-between gap-2"><div className="flex items-center gap-2"><b>{row.ticker}</b><EventIcon event={row.stage}/></div><Badge data-tone={grade.tone}>{grade.label}</Badge></div><div className="validation-line"><span>Scout</span><span>+1m</span><span>+5m</span><span>+15m</span></div><div className="validation-line metric"><span>{pct(row.move_at_detection_pct)}</span><span>{pct(row.max_1m_pct)}</span><span>{pct(row.max_5m_pct)}</span><span>{pct(row.max_15m_pct)}</span></div></button>;
  })}</> : <EmptyPane text="Validation outcomes will populate from live findings"/>}</div></div>;
}

function AttentionInbox({ items, onOpen, onStatus }: { items:AttentionItem[]; onOpen:(item:AttentionItem)=>void; onStatus:(item:AttentionItem,status:AttentionStatus)=>void }) {
  const visible=items.filter(item=>!['dismissed','expired'].includes(item.status));
  return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconBellFilled size={13}/>} title="OPPORTUNITY INBOX" subtitle={`${visible.filter(x=>x.status==='unread').length} new · ${visible.filter(x=>x.status==='watching').length} watching`}/><div className="min-h-0 flex-1 overflow-y-auto">
    {visible.length===0?<EmptyPane text="No opportunities need attention"/>:visible.map(item=>{const f=item.finding;return <article className="attention-row" data-priority={item.priority>=90||undefined} key={item.id}><div className="flex justify-between gap-2"><span className="flex min-w-0 items-center gap-2"><b>{f.ticker}</b><EventIcon event={f.stage}/>{f.leg_context&&<Badge data-tone="green">{f.leg_context.replaceAll('_',' ')}</Badge>}</span><span className="text-[9px] scout-muted">{age(item.updated_at)}</span></div><div className="attention-metrics">{money(f.price)} · RVOL {f.vol_ratio_15s?.toFixed(1)??'—'}× · {pct(f.change_15s_pct,1)} / 15s · {compactMoney(f.dollar_volume_15s)} / {f.trades_15s??'—'} trades</div><div className="attention-actions"><button onClick={()=>onOpen(item)}>Open</button><button data-active={item.status==='watching'||undefined} onClick={()=>onStatus(item,item.status==='watching'?'acknowledged':'watching')}>{item.status==='watching'?'Unwatch':'Watch'}</button><button onClick={()=>onStatus(item,'dismissed')}>Dismiss</button></div></article>})}
  </div></div>;
}

function SettingsPanel({ connected, onNotifications, scanner, saveScanner, scannerBusy, scannerMessage, backendVersion }: { connected:boolean; onNotifications:()=>void; scanner:ScannerSettings; saveScanner:(value:ScannerSettings)=>Promise<void>; scannerBusy:boolean; scannerMessage:string; backendVersion?:string }) {
  const [autostart,setAutostartState]=useState<{supported:boolean;enabled:boolean}>({supported:false,enabled:false});
  const [autostartBusy,setAutostartBusy]=useState(false);
  const [uiPrefs,setUiPrefs]=useState<UiPreferences>(DEFAULT_UI_PREFS);
  const [range,setRange]=useState(scanner);
  useEffect(()=>setRange(scanner),[scanner.min_price,scanner.max_price]);
  useEffect(()=>{
    let alive=true;
    void getNativeAutostartState().then(value=>{if(alive)setAutostartState(value);});
    const next=readUiPreferences();
    setUiPrefs(next);
    applyUiPreferences(next);
    return()=>{alive=false;};
  },[]);
  async function toggleAutostart(enabled:boolean){
    setAutostartBusy(true);
    const ok=await setNativeAutostart(enabled);
    if(ok)setAutostartState({supported:true,enabled});
    setAutostartBusy(false);
  }
  function setUi<K extends keyof UiPreferences>(key:K,value:UiPreferences[K]){
    setUiPrefs(current=>{const next={...current,[key]:value};persistUiPreferences(next);return next;});
  }
  return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconSettings size={14}/>} title="SETTINGS"/><div className="space-y-2 p-3">
    <div className="settings-row"><div><span>Scout API</span><div className="text-[10px] scout-muted">{connected ? "Live workstation data" : API_CONFIGURED ? "Connection unavailable" : "Demo mode"}</div></div><Badge data-tone={connected?"green":"orange"}>{connected?"CONNECTED":API_CONFIGURED?"OFFLINE":"DEMO"}</Badge></div>
    <div className="settings-row"><div><span>Launch with Windows</span><div className="text-[10px] scout-muted">{autostart.supported?"Keep Scout available after sign-in":"Available in the installed Windows app"}</div></div><Switch checked={autostart.enabled} disabled={!autostart.supported||autostartBusy} onCheckedChange={toggleAutostart}/></div>
    <div className="settings-row"><div><span>Close to tray</span><div className="text-[10px] scout-muted">Installed desktop app keeps its live connection running</div></div><Badge data-tone="green">ON</Badge></div>
    <div className="scanner-range-card"><div className="scanner-range-title"><div><b>Scanner price range</b><span>Controls subscriptions, detections, and alerts</span></div><Badge data-tone="blue">${range.min_price.toFixed(2)}–${range.max_price.toFixed(2)}</Badge></div><div className="scanner-range-inputs"><label>Minimum<Input type="number" min="0.01" step="0.01" value={range.min_price} onChange={e=>setRange({...range,min_price:Number(e.target.value)})}/></label><label>Maximum<Input type="number" min="0.02" step="0.01" value={range.max_price} onChange={e=>setRange({...range,max_price:Number(e.target.value)})}/></label></div><div className="scanner-presets"><Button variant="ghost" onClick={()=>setRange({min_price:.15,max_price:5})}>$0.15–$5</Button><Button variant="ghost" onClick={()=>setRange({min_price:.15,max_price:10})}>$0.15–$10</Button><Button variant="ghost" onClick={()=>setRange({min_price:2,max_price:10})}>$2–$10</Button></div><Button className="w-full" disabled={scannerBusy||range.min_price>=range.max_price} onClick={()=>void saveScanner(range)}>{scannerBusy?"Applying…":"Apply scanner range"}</Button>{scannerMessage&&<div className="notice-box">{scannerMessage}</div>}</div>
    <div className="settings-row"><span>Compact density</span><Switch checked={uiPrefs.compactDensity} onCheckedChange={value=>setUi("compactDensity",value)}/></div>
    <div className="settings-section-title">CHART</div>
    <div className="settings-row"><div><span>Event annotations</span><div className="text-[10px] scout-muted">Master control for selected-event overlays</div></div><Switch checked={uiPrefs.showChartMarkers} onCheckedChange={value=>setUi("showChartMarkers",value)}/></div>
    <div className="settings-row"><span>Formation region</span><Switch checked={uiPrefs.showFormationRegion} onCheckedChange={value=>setUi("showFormationRegion",value)}/></div>
    <div className="settings-row"><span>Detection price</span><Switch checked={uiPrefs.showDetectionPrice} onCheckedChange={value=>setUi("showDetectionPrice",value)}/></div>
    <div className="settings-row"><span>Trigger level</span><Switch checked={uiPrefs.showTriggerLevel} onCheckedChange={value=>setUi("showTriggerLevel",value)}/></div>
    <div className="settings-row"><span>Invalidation level</span><Switch checked={uiPrefs.showInvalidationLevel} onCheckedChange={value=>setUi("showInvalidationLevel",value)}/></div>
    <div className="settings-row"><span>Open detection timeframe</span><Switch checked={uiPrefs.openDetectionTimeframe} onCheckedChange={value=>setUi("openDetectionTimeframe",value)}/></div>
    <div className="settings-row"><span>Center selected detection</span><Switch checked={uiPrefs.autoCenterDetection} onCheckedChange={value=>setUi("autoCenterDetection",value)}/></div>
    <div className="settings-row"><div><span>Application version</span><div className="text-[10px] scout-muted">Desktop/PWA {CLIENT_VERSION} · Backend {backendVersion??'—'}</div></div><Badge data-tone={!backendVersion||backendVersion===CLIENT_VERSION?'green':'red'}>{!backendVersion||backendVersion===CLIENT_VERSION?'SYNCED':'MISMATCH'}</Badge></div>
    <Button className="mt-3 w-full" onClick={onNotifications}>Notification preferences</Button>
  </div></div>;
}

function EmptyPane({ text }: { text:string }) {
  return <div className="empty-pane"><IconActivity size={18}/><span>{text}</span></div>;
}

function PrimarySidebar({ view, findings, gainers, halts, catalysts, validation, selected, onSelect, connected, onNotifications, scanner, saveScanner, scannerBusy, scannerMessage, attention, setAttentionStatus, backendVersion }: {
  view:ActivityView; findings:Finding[]; gainers:Gainer[]; halts:Halt[]; catalysts:Catalyst[]; validation:ValidationRow[];
  selected?:Finding; onSelect:(f:Finding)=>void; connected:boolean; onNotifications:()=>void;
  scanner:ScannerSettings; saveScanner:(value:ScannerSettings)=>Promise<void>; scannerBusy:boolean; scannerMessage:string;
  attention:AttentionItem[]; setAttentionStatus:(item:AttentionItem,status:AttentionStatus)=>Promise<void>; backendVersion?:string;
}) {
  if (view === "radar") return <MarketPulse findings={findings} gainers={gainers} halts={halts} selectedId={selected?.id} onSelect={onSelect}/>;
  if (view === "ross") { const rows=findings.filter(f=>f.ross_match).sort((a,b)=>(b.ross_score||0)-(a.ross_score||0)); return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconFlame size={14}/>} title="ROSS SCREENER" subtitle={`${rows.length} matches`}/><div className="min-h-0 flex-1 overflow-y-auto">{rows.length?rows.map(f=><FindingRow key={f.ticker} finding={f} selected={selected?.id===f.id} onSelect={()=>onSelect(f)}/>):<EmptyPane text="No stocks currently meet the Ross criteria"/>}</div></div>; }
  if (view === "catalysts") return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconDiamondFilled size={12}/>} title="CATALYSTS"/><CatalystList catalysts={catalysts} findings={findings} onSelect={onSelect}/></div>;
  if (view === "gainers") return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconTrendingUp size={14}/>} title="TOP GAINERS"/><div className="min-h-0 flex-1 overflow-y-auto"><GainerRows gainers={gainers} findings={findings} onSelect={onSelect}/></div></div>;
  if (view === "halts") return <div className="flex h-full min-h-0 flex-col"><PanelTitle icon={<IconPlayerPauseFilled size={12}/>} title="HALTED"/><div className="min-h-0 flex-1 overflow-y-auto"><HaltRows halts={halts} findings={findings} onSelect={onSelect}/></div></div>;
  if (view === "validation") return <ValidationPanel rows={validation} findings={findings} selectedId={selected?.id} onSelect={onSelect}/>;
  if (view === "alerts") return <AttentionInbox items={attention} onOpen={item=>{onSelect(item.finding);void setAttentionStatus(item,'opened');}} onStatus={(item,status)=>void setAttentionStatus(item,status)}/>;
  return <SettingsPanel connected={connected} onNotifications={onNotifications} scanner={scanner} saveScanner={saveScanner} scannerBusy={scannerBusy} scannerMessage={scannerMessage} backendVersion={backendVersion}/>;
}

function ChartGroup({ finding, allFindings, onSelectFinding, onClose, onMaximize, maximized, active, pinned, onTogglePin, onActivate, pollOffsetMs, refreshNonce, tabLimit }: {
  finding?:Finding; allFindings:Finding[]; onSelectFinding:(f:Finding)=>void; onClose?:()=>void; onMaximize:()=>void; maximized:boolean;
  active:boolean; onActivate:()=>void; pollOffsetMs:number; refreshNonce:number; tabLimit:number;
  pinned:boolean; onTogglePin:()=>void;
}) {
  const [frozen,setFrozen]=useState(false);
  const [timeframe,setTimeframe]=useState<15|30|60|300>(15);
  useEffect(()=>{const detected=finding?.detection_timeframe_seconds;if(detected&&[15,30,60,300].includes(detected)&&readUiPreferences().openDetectionTimeframe)setTimeframe(detected as 15|30|60|300);},[finding?.id]);
  const signals=Array.from(new Set([finding?.stage, ...(finding?.signals || [])].filter(Boolean) as string[])).slice(0,4);
  const tabs=Array.from(new Map(allFindings.map(f=>[f.ticker,f])).values()).slice(0,tabLimit);
  return <section className="chart-group" data-active={active||undefined} onPointerDown={onActivate}>
    <div className="editor-tabs">
      <div className="editor-tab-list">
        {tabs.map(f=><button key={f.id} type="button" data-active={f.ticker===finding?.ticker || undefined} onClick={()=>onSelectFinding(f)} className="editor-tab"><span>{f.ticker}</span><span className="metric text-[10px] text-[var(--green)]">{pct(f.extension_pct,1)}</span></button>)}
      </div>
      <div className="editor-toolbar">
        <IconButton label={pinned?"Unpin chart":"Pin chart"} active={pinned} onClick={onTogglePin}><IconPin size={14}/></IconButton>
        <IconButton label={frozen ? "Show live chart" : "Show frozen detection chart"} active={frozen} onClick={()=>setFrozen(v=>!v)}><IconHistory size={14}/></IconButton>
        <IconButton label={maximized ? "Restore chart group" : "Maximize chart group"} onClick={onMaximize}>{maximized?<IconMinimize size={14}/>:<IconMaximize size={14}/>}</IconButton>
        {onClose && <IconButton label="Close chart group" onClick={onClose}><IconX size={14}/></IconButton>}
      </div>
    </div>
    <div className="chart-header">
      <div className="flex min-w-0 items-center gap-2"><b className="ticker-symbol">{finding?.ticker ?? "—"}</b>{finding && <><span className="metric text-[12px]">{money(finding.price)}</span><span className="metric text-[11px] text-[var(--green)]">{pct(finding.extension_pct)}</span>{signals.map(signal=><EventIcon key={signal} event={signal}/>)}</>}</div>
      <div className="flex items-center gap-1"><Select className="timeframe-select" label="Chart timeframe" value={String(timeframe)} onValueChange={value=>setTimeframe(Number(value) as 15|30|60|300)} options={[{value:"15",label:"15s"},{value:"30",label:"30s"},{value:"60",label:"1m"},{value:"300",label:"5m"}]}/><span className="chart-mode-label">{frozen?"DETECTION":"LIVE"}</span><IconButton label="Chart menu"><IconDots size={14}/></IconButton></div>
    </div>
    <div className="min-h-0 flex-1 overflow-hidden"><LiveChart finding={finding} frozen={frozen} active={active} pollOffsetMs={pollOffsetMs} refreshNonce={refreshNonce} timeframeSeconds={timeframe} showAnnotations={readUiPreferences().showChartMarkers} annotations={{formation:readUiPreferences().showFormationRegion,detection:readUiPreferences().showDetectionPrice,trigger:readUiPreferences().showTriggerLevel,invalidation:readUiPreferences().showInvalidationLevel}} onSelectFinding={onSelectFinding}/></div>
  </section>;
}

function ChartWorkspace({ findings, selected, onSelect }: { findings:Finding[]; selected?:Finding; onSelect:(f:Finding)=>void }) {
  const [mode,setMode]=useState<GroupMode>(4);
  const [maximized,setMaximized]=useState<string|undefined>();
  const [activeGroup,setActiveGroup]=useState("g1");
  const [refreshNonce,setRefreshNonce]=useState(0);
  const uniqueFindings=useMemo(()=>Array.from(new Map(findings.map(f=>[f.ticker,f])).values()),[findings]);
  const [groups,setGroups]=useState<ChartGroupState[]>([
    {id:"g1",ticker:uniqueFindings[0]?.ticker},{id:"g2",ticker:uniqueFindings[1]?.ticker},{id:"g3",ticker:uniqueFindings[2]?.ticker},{id:"g4",ticker:uniqueFindings[3]?.ticker},
  ]);

  useEffect(()=>{
    if (!selected) return;
    const existing=groups.find(g=>g.ticker===selected.ticker);
    if(existing){if(activeGroup!==existing.id)setActiveGroup(existing.id);return;}
    const target=groups.find(g=>g.id===activeGroup&&!g.pinned) ?? groups.find(g=>!g.pinned);
    if(!target)return;
    if(activeGroup!==target.id)setActiveGroup(target.id);
    setGroups(current=>current.map(g=>g.id===target.id?{...g,ticker:selected.ticker}:g));
  },[selected?.id,activeGroup,groups]);

  useEffect(()=>{setGroups(current=>current.map((group,index)=>group.ticker?group:{...group,ticker:uniqueFindings[index]?.ticker}));},[uniqueFindings]);

  function resolve(group:ChartGroupState){return findings.find(f=>f.ticker===group.ticker);}
  function ensureMode(next:GroupMode){
    setMode(next); setMaximized(undefined);
    setGroups(current=>{
      const copy=[...current];
      while(copy.length<next){const f=uniqueFindings[copy.length%Math.max(1,uniqueFindings.length)]; copy.push({id:`g${copy.length+1}`,ticker:f?.ticker});}
      return copy.slice(0,next);
    });
  }
  function setGroupTicker(id:string,f:Finding){setGroups(current=>current.map(g=>g.id===id?{...g,ticker:f.ticker}:g)); onSelect(f);}
  function togglePin(id:string){setGroups(current=>current.map(g=>g.id===id?{...g,pinned:!g.pinned}:g));}
  function closeGroup(id:string){setGroups(current=>{const next=current.filter(g=>g.id!==id); const m=(next.length<=1?1:next.length<=2?2:4) as GroupMode; setMode(m); return next.length?next:[{id:"g1",ticker:uniqueFindings[0]?.ticker}];}); setMaximized(undefined);}

  const visible = maximized ? groups.filter(g=>g.id===maximized) : groups.slice(0,mode);
  const render = (g:ChartGroupState, closable=true)=><ChartGroup key={g.id} finding={resolve(g)} allFindings={findings} onSelectFinding={f=>setGroupTicker(g.id,f)} onClose={closable?()=>closeGroup(g.id):undefined} onMaximize={()=>setMaximized(maximized===g.id?undefined:g.id)} maximized={maximized===g.id} active={activeGroup===g.id} pinned={Boolean(g.pinned)} onTogglePin={()=>togglePin(g.id)} onActivate={()=>setActiveGroup(g.id)} pollOffsetMs={Math.max(0,groups.findIndex(x=>x.id===g.id))*650} refreshNonce={refreshNonce} tabLimit={mode===4?5:mode===2?7:10}/>;

  return <div className="chart-workspace">
    <div className="workspace-toolbar">
      <div className="flex items-center gap-1.5"><span className="workspace-label">CHART WORKSPACE</span><span className="scout-muted text-[10px]">editor groups</span></div>
      <div className="flex items-center gap-0.5"><IconButton label="Single chart" active={mode===1} onClick={()=>ensureMode(1)}><IconLayoutDashboard size={14}/></IconButton><IconButton label="Split right" active={mode===2} onClick={()=>ensureMode(2)}><IconLayoutColumns size={14}/></IconButton><IconButton label="Four chart grid" active={mode===4} onClick={()=>ensureMode(4)}><IconLayoutGrid size={14}/></IconButton><IconButton label="Refresh charts" onClick={()=>setRefreshNonce(v=>v+1)}><IconRefresh size={14}/></IconButton></div>
    </div>
    <div className="min-h-0 flex-1">
      {!visible.length && <EmptyPane text="Waiting for findings"/>}
      {visible.length===1 && render(visible[0], false)}
      {visible.length===2 && <Group orientation="horizontal" className="h-full"><Panel id="chart-a" defaultSize="50%" minSize="25%"><div className="h-full pr-[2px]">{render(visible[0])}</div></Panel><Separator className="chart-gutter chart-gutter-v"/><Panel id="chart-b" defaultSize="50%" minSize="25%"><div className="h-full pl-[2px]">{render(visible[1])}</div></Panel></Group>}
      {visible.length>=3 && <Group orientation="horizontal" className="h-full"><Panel id="chart-left" defaultSize="50%" minSize="25%"><Group orientation="vertical" className="h-full"><Panel id="chart-1" defaultSize="50%" minSize="25%"><div className="h-full pb-[2px]">{render(visible[0])}</div></Panel><Separator className="chart-gutter chart-gutter-h"/><Panel id="chart-3" defaultSize="50%" minSize="25%"><div className="h-full pt-[2px]">{render(visible[2] ?? visible[0])}</div></Panel></Group></Panel><Separator className="chart-gutter chart-gutter-v"/><Panel id="chart-right" defaultSize="50%" minSize="25%"><Group orientation="vertical" className="h-full"><Panel id="chart-2" defaultSize="50%" minSize="25%"><div className="h-full pb-[2px]">{render(visible[1])}</div></Panel><Separator className="chart-gutter chart-gutter-h"/><Panel id="chart-4" defaultSize="50%" minSize="25%"><div className="h-full pt-[2px]">{render(visible[3] ?? visible[1])}</div></Panel></Group></Panel></Group>}
    </div>
  </div>;
}

function Inspector({ finding, onNotifications }: { finding?:Finding; onNotifications:()=>void }) {
  const [tab,setTab]=useState<"overview"|"pattern"|"verification"|"history">("overview");
  const [verification,setVerification]=useState<FindingVerification|null>(null);
  const [reviewGrade,setReviewGrade]=useState<number>(5);
  useEffect(()=>{setVerification(null);if(finding&&finding.id>0&&!finding.selection_context&&API_CONFIGURED)void getFindingVerification(finding.id).then(setVerification).catch(()=>setVerification(null));},[finding?.id,finding?.selection_context]);
  if(!finding) return <EmptyPane text="Select a Scout finding"/>;
  if(finding.selection_context)return <div className="h-full overflow-y-auto p-3"><div className="flex flex-wrap items-center gap-1.5"><b className="ticker-symbol text-[16px]">{finding.ticker}</b><Badge data-tone="blue"><IconPin size={10}/> SELECTED</Badge><EventIcon event={finding.stage}/></div><div className="mt-3 context-selection"><b>{finding.selection_title}</b><span>{finding.selection_detail}</span></div><InspectorSection title="EVENT CONTEXT"><KV k="Type" v={finding.selection_context.replaceAll("_"," ").toUpperCase()}/><KV k="Timestamp" v={new Date(finding.detected_at*1000).toLocaleString([],{timeZone:"America/New_York",hour12:true})+" ET"}/><KV k="Price" v={money(finding.price)}/><div className="notice-box">No Scout detection is being implied. This Inspector view reflects the panel item you selected.</div></InspectorSection><InspectorSection title="RELATED MARKET CONTEXT"><div className="pattern-snapshot"><LiveChart finding={finding} active={false} timeframeSeconds={15}/></div></InspectorSection></div>;
  const signals=Array.from(new Set([finding.stage,...(finding.signals||[])]));
  return <div className="h-full overflow-y-auto p-3">
    <div className="flex items-start justify-between gap-2"><div><div className="flex flex-wrap items-center gap-1.5"><b className="ticker-symbol text-[16px]">{finding.ticker}</b><Badge data-tone="blue"><IconPin size={10}/> SELECTED</Badge>{signals.map(signal=><EventIcon key={signal} event={signal}/>)}<Badge data-tone={finding.quality_label==="CLEAN"?"green":finding.quality_label==="DEVELOPING"?"blue":finding.quality_label==="CHOPPY"?"orange":"red"}>{finding.actionable_rank||"C"} · {finding.quality_label||"UNRATED"}</Badge></div><div className="mt-1 flex items-baseline gap-2"><span className="metric text-[16px] font-semibold">{money(finding.price)}</span><span className="metric text-[11px] text-[var(--green)]">{pct(finding.extension_pct)}</span></div><div className="mt-1 text-[10px] scout-muted">Inspector follows your selection only · detected {age(finding.detected_at)} · evidence {finding.score}/10</div></div><IconButton label="Notification settings" onClick={onNotifications}><IconBellFilled size={14}/></IconButton></div>
    <div className="inspector-tabs">{(["overview","pattern","verification","history"] as const).map(value=><Button key={value} variant="ghost" data-active={tab===value||undefined} onClick={()=>setTab(value)}>{value}</Button>)}</div>
    {tab==="pattern"&&<><InspectorSection title="PATTERN SNAPSHOT"><div className="pattern-snapshot"><LiveChart finding={finding} frozen={Boolean(finding.chart_url)} active={false} timeframeSeconds={(finding.detection_timeframe_seconds&&[15,30,60,300].includes(finding.detection_timeframe_seconds)?finding.detection_timeframe_seconds:15) as 15|30|60|300}/></div><KV k="Formation" v={(finding.leg_context||finding.stage).replaceAll("_"," ")}/><KV k="Detection" v={`${clock(finding.detected_at)} · ${money(finding.price)}`}/><KV k="Detection timeframe" v={`${finding.detection_timeframe_seconds||15}s`}/><KV k="Formation range" v={`${money(finding.formation_low)}–${money(finding.formation_high)}`}/><KV k="Trigger" v={money(finding.trigger_level||finding.breakout_level)}/><KV k="Invalidation" v={money(finding.invalidation_level)}/><KV k="Status" v={finding.urgency||"WATCH"}/></InspectorSection></>}
    {tab==="verification"&&<><InspectorSection title="DETECTION VERDICT"><KV k="Detected" v="YES"/><KV k="Timestamp" v={new Date(finding.detected_at*1000).toLocaleString([], {timeZone:"America/New_York",hour12:true})+" ET"}/><KV k="Price / signal" v={`${money(finding.price)} · ${finding.stage}`}/><KV k="Engine" v={finding.engine_version||"Legacy"}/><KV k="Source" v={(finding.hybrid_sources?.length?finding.hybrid_sources.join(" + "):finding.engine_source||"python").toUpperCase()}/>{finding.notification_reason&&<KV k="Why now" v={finding.notification_reason}/>}<div className="verification-grade"><span>{verification?.automatic_grade?"★".repeat(verification.automatic_grade)+"☆".repeat(5-verification.automatic_grade):"Outcome pending"}</span><b>{verification?.automatic_label||"PROVISIONAL"}</b></div>{verification?.grade_reasons?.map(reason=><div className="evidence-item" key={reason}><span className="evidence-dot"/>{reason}</div>)}</InspectorSection><InspectorSection title="OUTCOME COMPARISON"><div className="comparison-grid"><div><b>AT DETECTION</b><span>{money(finding.price)}</span><small>{finding.stage} · {finding.detection_timeframe_seconds||15}s</small></div><div><b>AFTERWARD</b><span>{pct(verification?.outcome?.max_15m_pct)}</span><small>15-minute maximum</small></div></div><KV k="+1 minute" v={pct(verification?.outcome?.max_1m_pct)}/><KV k="+5 minutes" v={pct(verification?.outcome?.max_5m_pct)}/><KV k="+15 minutes" v={pct(verification?.outcome?.max_15m_pct)}/><KV k="Session maximum" v={pct(verification?.outcome?.max_session_pct)}/></InspectorSection><InspectorSection title="DELIVERY VERDICT">{verification?.legacy_delivery_audit?<div className="notice-box">Legacy record — delivery auditing unavailable</div>:verification?.delivery.map(event=><div className="delivery-event" key={event.id}><span>{clock(event.event_at)}</span><b>{event.channel}</b><Badge data-tone={event.status.includes("failed")?"red":event.status==="provider_accepted"?"green":"blue"}>{event.status.replaceAll("_"," ")}</Badge></div>)}</InspectorSection><InspectorSection title="YOUR EVALUATION"><div className="star-picker">{[1,2,3,4,5].map(star=><Button key={star} variant="ghost" data-active={reviewGrade>=star||undefined} onClick={()=>setReviewGrade(star)}>★</Button>)}</div><Button className="w-full" onClick={()=>void saveFindingReview(finding.id,{user_grade:reviewGrade,user_agrees:reviewGrade===verification?.automatic_grade}).then(setVerification)}>Save evaluation</Button></InspectorSection></>}
    {tab==="history"&&<InspectorSection title="EVENT HISTORY"><KV k="Episode" v={`#${finding.episode_id??0}`}/><KV k="Selected event" v={`${finding.stage} · ${clock(finding.detected_at)}`}/><div className="notice-box">Historical events remain tied to this ticker and episode; selecting one restores its original chart context.</div></InspectorSection>}
    {tab==="overview"&&<>
    {(finding.recipe_score!=null||finding.lifecycle_phase)&&<InspectorSection title="PRE-IGNITION AUDIT"><KV k="Lifecycle" v={`${finding.lifecycle_phase||"UNCLASSIFIED"}${finding.shadow_mode?" · SHADOW":""}`}/><KV k="Recipe" v={`${finding.recipe_score??0}/10`}/><KV k="Timeliness" v={(finding.timeliness_label||"PENDING").replaceAll("_"," ")}/><KV k="Trigger distance" v={finding.trigger_distance_pct==null?"—":`${finding.trigger_distance_pct>=0?"":"+"}${Math.abs(finding.trigger_distance_pct).toFixed(2)}% ${finding.trigger_distance_pct>=0?"below":"through"}`}/><KV k="Base extension" v={finding.base_extension_at_detection_pct==null?"—":pct(finding.base_extension_at_detection_pct)}/>{finding.recipe_present?.map(item=><div className="evidence-item" key={`present-${item}`}><span className="evidence-dot"/>{item}</div>)}{finding.recipe_missing?.map(item=><div className="quality-rejection" key={`missing-${item}`}>Missing · {item}</div>)}{finding.shadow_mode&&<div className="notice-box">Shadow calibration only. This event is persisted and plotted but cannot send a notification.</div>}</InspectorSection>}
    {finding.leg_context&&<InspectorSection title="FIRST LEG"><KV k="Release context" v={finding.leg_context.replaceAll("_"," ")}/><KV k="Detection price" v={money(finding.price)}/><KV k="Detected at" v={clock(finding.detected_at)}/><KV k="Alert age" v={age(finding.detected_at)}/></InspectorSection>}
    {finding.halt_pressure_score?<InspectorSection title="UPWARD HALT PRESSURE"><KV k="Evidence score" v={`${finding.halt_pressure_score}/100`}/><KV k="Status" v={finding.halt_pressure_score>=82?"IMMEDIATE REVIEW":finding.halt_pressure_score>=65?"WATCH":"NORMAL"}/><div className="notice-box">Evidence score only; the exchange feed confirms actual Limit States and halts.</div></InspectorSection>:null}
    {finding.ross_match&&<InspectorSection title="ROSS CRITERIA"><KV k="Match" v="PASS"/><KV k="Criteria score" v={`${finding.ross_score??0}/100`}/></InspectorSection>}
    <InspectorSection title="CANDIDATE PROFILE"><CandidateProfileChart finding={finding}/></InspectorSection>
    <InspectorSection title="MARKET QUALITY"><QualityGauge finding={finding}/><KV k="Directional efficiency" v={finding.directional_efficiency==null?"—":`${(finding.directional_efficiency*100).toFixed(0)}%`}/><KV k="Active intervals" v={finding.active_bucket_ratio==null?"—":`${(finding.active_bucket_ratio*100).toFixed(0)}%`}/><KV k="Direction reversals" v={`${finding.direction_reversals??"—"}`}/>{finding.rejection_reasons?.map(reason=><div key={reason} className="quality-rejection">{reason}</div>)}</InspectorSection>
    {finding.reversal_phase && <InspectorSection title="REVERSAL EPISODE"><KV k="Phase" v={finding.reversal_phase}/><KV k="Episode" v={`#${finding.episode_id??0}`}/><KV k="Local low" v={money(finding.reversal_low)}/><KV k="Prior drawdown" v={finding.reversal_drawdown_pct==null?"—":`-${finding.reversal_drawdown_pct.toFixed(2)}%`}/></InspectorSection>}
    <InspectorSection title="VELOCITY"><VelocityChart finding={finding}/><KV k="Acceleration" v={finding.accel_15s_pp == null ? "—" : `${finding.accel_15s_pp>=0?"+":""}${finding.accel_15s_pp.toFixed(2)}pp`}/></InspectorSection>
    <InspectorSection title="PARTICIPATION"><ParticipationChart finding={finding}/><KV k="Day volume" v={compactMoney(finding.day_volume)}/><KV k="Projected session" v={compactMoney(finding.projected_session_volume)}/><KV k="Recent shares/min" v={compactMoney(finding.volume_rate_per_minute)}/></InspectorSection>
    <InspectorSection title="DAILY CONTEXT"><KV k="Previous close" v={money(finding.previous_close)}/><KV k="Gap / day change" v={pct(finding.gap_pct)}/><KV k="Float" v={finding.float_shares==null?"Awaiting trusted source":compactMoney(finding.float_shares)}/></InspectorSection>
    <InspectorSection title="STRUCTURE"><KV k="EMA9" v={money(finding.ema9)}/><KV k="EMA21" v={money(finding.ema21)}/><KV k="VWAP" v={`${money(finding.vwap)} ${finding.above_vwap?"↑":"↓"}`}/><KV k="Range" v={finding.quiet_break?"Break ✓":"Pressing"}/><KV k="Breakout" v={finding.breakout_level != null ? `${money(finding.breakout_level)} · ${finding.breakout_window || "range"}` : "—"}/></InspectorSection>
    <InspectorSection title="WHY SCOUT FLAGGED">{finding.evidence.length ? finding.evidence.map(e=><div key={e} className="evidence-item"><span className="evidence-dot"/>{e}</div>) : <div className="text-[10px] scout-muted">No evidence snapshot stored.</div>}</InspectorSection>
    {finding.catalyst_headline && <InspectorSection title="CATALYST"><div className="catalyst-card"><div className="flex items-center gap-2"><IconDiamondFilled size={10} className="text-[var(--cyan)]"/><Badge data-tone="green">{finding.catalyst_category || "Bullish"} {finding.catalyst_score}</Badge></div><div className="mt-2 text-[11px] leading-relaxed">{finding.catalyst_headline}</div></div></InspectorSection>}
    </>}
  </div>;
}

function KV({k,v}:{k:string;v:string}) { return <div className="inspector-kv"><span className="scout-muted">{k}</span><span className="metric">{v}</span></div>; }
function InspectorSection({title,children}:{title:string;children:React.ReactNode}) { return <section className="inspector-section"><div className="inspector-section-title">{title}</div>{children}</section>; }

function TimelineList({ timeline, selectedTicker, findings, onSelect }: { timeline:TimelineItem[]; selectedTicker?:string; findings:Finding[]; onSelect:(finding:Finding)=>void }) {
  const items=selectedTicker ? timeline.filter(item=>item.ticker===selectedTicker) : timeline;
  if(!items.length) return <EmptyPane text="No timeline events yet"/>;
  return <div className="dock-content">{items.slice(0,120).map((item,index)=>{
    const f=item.type==="finding" ? item.payload as Finding : null;
    const c=item.type==="catalyst" ? item.payload as Catalyst : null;
    const h=(item.type==="halt"||item.type==="resume") ? item.payload as Halt : null;
    const label=f ? `${f.stage}${f.extension_pct!=null?` · ${pct(f.extension_pct)}`:""}` : c ? `${c.category || "Catalyst"} · ${c.headline}` : h ? `${item.type.toUpperCase()} · ${h.reason_code || h.status_code}` : item.type;
    const selection=f??findings.find(candidate=>candidate.ticker===item.ticker)??(c?contextualFinding({kind:"catalyst",ticker:item.ticker,title:c.headline,detail:`${c.category} · ${c.source}`,at:item.at,stage:"CATALYST_WATCH",id:-c.id}):contextualFinding({kind:"halt",ticker:item.ticker,title:`${item.type} · ${item.ticker}`,detail:label,at:item.at,stage:item.type.toUpperCase()}));
    return <button className="dock-line" key={`${item.type}-${item.at}-${item.ticker}-${index}`} onClick={()=>onSelect(selection)}><span className="event-time">{clock(item.at)}</span><span className={`event-icon event-${item.type}`}>{eventGlyph(item.type,item.payload)}</span><b>{item.ticker}</b><span className="truncate">{label}</span></button>;
  })}</div>;
}

function ValidationTable({ rows, findings, onSelect }: { rows:ValidationRow[];findings:Finding[];onSelect:(finding:Finding)=>void }) {
  return <div className="validation-table"><div className="validation-head"><span>Ticker</span><span>Scout</span><span>+1m</span><span>+5m</span><span>+15m</span><span>Max</span><span>Grade</span></div>{rows.map(row=>{const grade=gradeValidation(row);const selection=findings.find(f=>f.id===row.id)||findings.find(f=>f.ticker===row.ticker)||contextualFinding({kind:"validation",ticker:row.ticker,title:`Validation · ${row.ticker}`,detail:`${grade.label} outcome review`,at:row.detected_at,price:row.price,stage:row.stage,id:row.id});return <button className="validation-row" key={row.id} onClick={()=>onSelect(selection)}><b>{row.ticker}</b><span>{pct(row.move_at_detection_pct)}</span><span>{pct(row.max_1m_pct)}</span><span>{pct(row.max_5m_pct)}</span><span>{pct(row.max_15m_pct)}</span><span>{pct(row.max_session_pct)}</span><Badge data-tone={grade.tone}>{grade.label}</Badge></button>;})}</div>;
}

function BottomDock({ tab, setTab, catalysts, findings, selected, status, validation, timeline, onSelect, onCollapse, onMaximize, maximized }: {
  tab:DockTab; setTab:(t:DockTab)=>void; catalysts:Catalyst[]; findings:Finding[]; selected?:Finding; status:ScoutStatus|null; validation:ValidationRow[]; timeline:TimelineItem[];onSelect:(finding:Finding)=>void;
  onCollapse:()=>void; onMaximize:()=>void; maximized:boolean;
}) {
  const tabs: {id:DockTab;label:string}[]=[{id:"catalysts",label:"Catalysts"},{id:"evidence",label:"Evidence"},{id:"validation",label:"Validation"},{id:"events",label:"Events"}];
  return <div className="flex h-full min-h-0 flex-col">
    <div className="dock-tabbar"><div className="flex min-w-0 items-center">{tabs.map(x=><button key={x.id} data-active={tab===x.id || undefined} onClick={()=>setTab(x.id)} className="dock-tab">{x.label}</button>)}</div><div className="ml-auto flex items-center"><IconButton label={maximized?"Restore panel":"Maximize panel"} onClick={onMaximize}>{maximized?<IconMinimize size={13}/>:<IconMaximize size={13}/>}</IconButton><IconButton label="Collapse panel" onClick={onCollapse}><IconChevronDown size={13}/></IconButton></div></div>
    <div className="min-h-0 flex-1 overflow-y-auto">
      {tab==="catalysts" && <CatalystList catalysts={catalysts} findings={findings} onSelect={onSelect}/>}
      {tab==="evidence" && <div className="dock-content">{selected ? selected.evidence.map(e=><div className="dock-line" key={e}><span className="event-time">{age(selected.detected_at)}</span><span className="text-[var(--blue)]">●</span><b>{selected.ticker}</b><span>{e}</span></div>) : <EmptyPane text="Select a finding"/>}</div>}
      {tab==="validation" && (validation.length ? <ValidationTable rows={validation} findings={findings} onSelect={onSelect}/> : <EmptyPane text="Validation outcomes will populate automatically"/>)}
      {tab==="events" && <TimelineList timeline={timeline} selectedTicker={selected?.ticker} findings={findings} onSelect={onSelect}/>}
    </div>
  </div>;
}

function SystemStat({label,value}:{label:string;value:string}) { return <div className="system-stat"><span>{label}</span><b>{value}</b></div>; }

function ActivityRail({ active, onActive, counts }: { active:ActivityView; onActive:(v:ActivityView)=>void; counts:Record<Exclude<ActivityView,"settings">,number> }) {
  const items: {id:Exclude<ActivityView,"settings">;label:string;icon:React.ReactNode;tone?:string}[]=[
    {id:"radar",label:"Radar",icon:<IconBolt size={18}/>,tone:"green"},
    {id:"ross",label:"Ross Screener",icon:<IconFlame size={17}/>},
    {id:"catalysts",label:"Catalysts",icon:<IconDiamondFilled size={15}/>},
    {id:"gainers",label:"Top gainers",icon:<IconTrendingUp size={18}/>},
    {id:"halts",label:"Halted",icon:<IconPlayerPauseFilled size={15}/>,tone:"red"},
    {id:"validation",label:"Validation",icon:<IconTableFilled size={15}/>,tone:"orange"},
    {id:"alerts",label:"Notifications",icon:<IconBellFilled size={15}/>,tone:"red"},
  ];
  return <aside className="activity-rail"><div className="activity-brand"><IconTargetArrow size={18}/></div><div className="activity-stack">{items.map(item=>{const count=counts[item.id];const label=`${item.label} · ${count} item${count===1?"":"s"}`;return <div className="rail-item" key={item.id}><IconButton label={label} active={active===item.id} onClick={()=>onActive(item.id)}>{item.icon}</IconButton>{count>0&&<span className="rail-count" data-tone={item.tone}>{count>99?"99+":count}</span>}</div>})}</div><div className="activity-bottom"><IconButton label="Settings" active={active==="settings"} onClick={()=>onActive("settings")}><IconSettings size={18}/></IconButton></div></aside>;
}

function NotificationSheet({ open, prefs, status, onClose, onChange, onSave, onTest, saving, testMessage }: {
  open:boolean; prefs:NotificationPreferences; status:ScoutStatus|null; onClose:()=>void; onChange:(p:NotificationPreferences)=>void; onSave:()=>void;
  onTest:(platform:"windows"|"android"|"email")=>void; saving:boolean; testMessage:string;
}) {
  const [notificationTab,setNotificationTab]=useState<"general"|"platforms"|"signals"|"sessions"|"behavior">("general");
  const [pushState,setPushState]=useState<WebPushState|null>(null);
  const [pushBusy,setPushBusy]=useState(false);
  useEffect(()=>{if(open)void webPushState().then(setPushState).catch(error=>setPushState({supported:false,configured:false,permission:"default",subscribed:false,message:error instanceof Error?error.message:"Unable to inspect Web Push"}));},[open]);
  async function togglePush(){setPushBusy(true);try{setPushState(await (pushState?.subscribed?disableWebPush():enableWebPush()));}catch(error){setPushState(current=>({...current!,message:error instanceof Error?error.message:"Unable to update Web Push"}));}finally{setPushBusy(false);}}
  if(!open)return null;
  const signals=["ACTIVITY_WATCH","REVERSAL_WATCH","FIRST_LEG_WATCH","PRE_IGNITION","AWAKENING","FIRST_LEG","RECLAIM","EMA_RECLAIM","VWAP_RECLAIM","FIRST_PULLBACK","EARLY","SURGE","BREAKOUT","STAIRCASE","IGNITION","HALT_WATCH","HALT_PRESSURE","CATALYST_WATCH","CATALYST_ACTIVE","HALT","RESUME","REARM"];
  const sessions=["overnight","premarket","regular","afterhours"];
  function setPlatform(platform:"windows"|"android"|"email",enabled:boolean){onChange({...prefs,platforms:{...prefs.platforms,[platform]:{...prefs.platforms[platform],enabled}} as NotificationPreferences["platforms"]});}
  return <div className="sheet-backdrop" onMouseDown={e=>{if(e.currentTarget===e.target)onClose();}}><aside className="notification-sheet">
    <PanelTitle icon={<IconBellFilled size={14}/>} title="NOTIFICATIONS" actions={<IconButton label="Close notifications" onClick={onClose}><IconX size={14}/></IconButton>}/>
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="notification-tabs">{(["general","platforms","signals","sessions","behavior"] as const).map(value=><Button key={value} variant="ghost" data-active={notificationTab===value||undefined} onClick={()=>setNotificationTab(value)}>{value}</Button>)}</div>
      {notificationTab==="general"&&<>
      <div className="settings-row"><div><b>Master notifications</b><div className="text-[10px] scout-muted">Delivery only; detection remains active.</div></div><Switch checked={prefs.master_enabled} onCheckedChange={v=>onChange({...prefs,master_enabled:v})}/></div>
      <div className="settings-row"><div><b>Grouped opportunity episodes</b><div className="text-[10px] scout-muted">One evolving alert per ticker</div></div><Switch checked={prefs.group_by_ticker} onCheckedChange={v=>onChange({...prefs,group_by_ticker:v})}/></div>
      </>}
      {notificationTab==="platforms"&&<>
      <div className="settings-section-title">PLATFORMS</div>
      <div className="notice-box"><b>Primary alert channel: Scout → ntfy</b><div>Desktop OS toasts are suppressed by default to avoid duplicate alerts. The in-app Attention center remains active.</div></div>
      <PlatformRow title="Windows native toast" subtitle="Paused in v6.5.3 · use Scout/ntfy to avoid duplicate OS alerts" enabled={false} available={false} onToggle={()=>{}} onTest={()=>onTest("windows")}/>
      <PlatformRow title="Mobile / ntfy" subtitle={status?.notifications.android_delivery_configured===false?"ntfy server channel not configured":"Primary background alert channel"} enabled={prefs.platforms.android.enabled} available={status?.notifications.android_delivery_configured!==false} onToggle={v=>setPlatform("android",v)} onTest={()=>onTest("android")}/>
      <div className="push-enrollment"><div><b>Optional PWA Web Push</b><small>{pushState?.message||"Checking this device…"} · leave disabled if ntfy is already installed on this phone.</small></div><Button disabled={pushBusy||!pushState?.supported||!pushState?.configured} onClick={()=>void togglePush()}>{pushBusy?"Working…":pushState?.subscribed?"Disable PWA push":"Enable PWA push"}</Button></div>
      <div className="settings-subgrid"><ToggleRow label="Sound preference" checked={prefs.platforms.android.sound} onChange={v=>onChange({...prefs,platforms:{...prefs.platforms,android:{...prefs.platforms.android,sound:v}}})}/><ToggleRow label="Vibration" checked={prefs.platforms.android.vibration} onChange={v=>onChange({...prefs,platforms:{...prefs.platforms,android:{...prefs.platforms.android,vibration:v}}})}/><PriorityRow label="Alert priority" value={prefs.platforms.android.priority} onChange={value=>onChange({...prefs,platforms:{...prefs.platforms,android:{...prefs.platforms.android,priority:value}}})}/></div>
      <div className="settings-row"><div><b>Email / Resend</b><div className="text-[10px] scout-muted">Paused for this release. No setup is required.</div></div><Badge data-tone="blue">PAUSED</Badge></div>
      </>}
      {notificationTab==="signals"&&<>
      <div className="settings-section-title">SIGNALS</div>
      {signals.map(signal=><div key={signal} className="signal-setting"><span>{signal.replaceAll("_"," ")}</span><Select label={`${signal} delivery`} value={prefs.signals[signal]??"notify"} onValueChange={value=>onChange({...prefs,signals:{...prefs.signals,[signal]:value as "notify"|"silent"|"off"}})} options={[{value:"notify",label:"Notify"},{value:"silent",label:"Silent"},{value:"off",label:"Off"}]}/></div>)}
      </>}
      {notificationTab==="sessions"&&<>
      <div className="settings-section-title">SESSIONS</div>
      <div className="settings-session-grid">{sessions.map(session=><ToggleRow key={session} label={session[0].toUpperCase()+session.slice(1)} checked={Boolean(prefs.sessions[session])} onChange={v=>onChange({...prefs,sessions:{...prefs.sessions,[session]:v}})}/>)}</div>
      </>}
      {notificationTab==="behavior"&&<>
      <div className="settings-section-title">BEHAVIOR</div>
      <label className="quality-profile-row"><span><b>Market quality</b><small>Controls promotion and notification strictness</small></span><Select label="Market quality" value={prefs.market_quality_profile||"balanced"} onValueChange={value=>onChange({...prefs,market_quality_profile:value as NotificationPreferences["market_quality_profile"]})} options={[{value:"balanced",label:"Balanced"},{value:"strict",label:"Strict"},{value:"permissive",label:"Permissive"}]}/></label>
      <ToggleRow label="Quiet hours" checked={prefs.quiet_hours.enabled} onChange={v=>onChange({...prefs,quiet_hours:{...prefs.quiet_hours,enabled:v}})}/>
      {prefs.quiet_hours.enabled && <div className="quiet-hours-grid"><label>From<input type="time" value={prefs.quiet_hours.start} onChange={e=>onChange({...prefs,quiet_hours:{...prefs.quiet_hours,start:e.target.value}})}/></label><label>To<input type="time" value={prefs.quiet_hours.end} onChange={e=>onChange({...prefs,quiet_hours:{...prefs.quiet_hours,end:e.target.value}})}/></label></div>}
      <ToggleRow label="Allow critical signals during quiet hours" checked={prefs.quiet_hours.allow_critical} onChange={v=>onChange({...prefs,quiet_hours:{...prefs.quiet_hours,allow_critical:v}})}/>
      <ToggleRow label="Only stage escalations" checked={prefs.only_stage_escalations} onChange={v=>onChange({...prefs,only_stage_escalations:v})}/>
      <ToggleRow label="Group by ticker" checked={prefs.group_by_ticker} onChange={v=>onChange({...prefs,group_by_ticker:v})}/>
      <label className="score-setting"><span>Minimum score</span><input type="number" min="0" max="20" value={prefs.minimum_score} onChange={e=>onChange({...prefs,minimum_score:Number(e.target.value)||0})}/></label>
      </>}
      {testMessage && <div className="notice-box">{testMessage}</div>}
    </div>
    <div className="sheet-footer"><Button variant="ghost" onClick={onClose}>Cancel</Button><Button onClick={onSave} disabled={saving}>{saving?"Saving…":"Save preferences"}</Button></div>
  </aside></div>;
}

function ToggleRow({label,checked,onChange}:{label:string;checked:boolean;onChange:(value:boolean)=>void}) { return <div className="toggle-row"><span>{label}</span><Switch checked={checked} onCheckedChange={onChange}/></div>; }
function PriorityRow({label,value,onChange}:{label:string;value:string;onChange:(value:string)=>void}) { return <label className="priority-row"><span>{label}</span><Select label={label} value={value||"high"} onValueChange={onChange} options={[{value:"low",label:"Low"},{value:"normal",label:"Normal"},{value:"high",label:"High"},{value:"critical",label:"Critical"}]}/></label>; }
function PlatformRow({title,subtitle,enabled,available=true,onToggle,onTest}:{title:string;subtitle:string;enabled:boolean;available?:boolean;onToggle:(v:boolean)=>void;onTest:()=>void}) { return <div className="settings-row"><div><b>{title}</b><div className="text-[10px] scout-muted">{subtitle}</div></div><div className="flex items-center gap-2"><Button variant="ghost" className="!min-h-7 !px-2 text-[10px]" disabled={!available} onClick={onTest}>Test</Button><Switch checked={enabled&&available} disabled={!available} onCheckedChange={onToggle}/></div></div>; }

function CommandPalette({ open, query, onQuery, findings, catalysts, gainers, onClose, onSelect }: {
  open:boolean; query:string; onQuery:(value:string)=>void; findings:Finding[]; catalysts:Catalyst[]; gainers:Gainer[]; onClose:()=>void; onSelect:(finding:Finding)=>void;
}) {
  const normalized=query.trim().toUpperCase();
  const candidates=useMemo(()=>{
    const byTicker=new Map<string,Finding>();
    findings.forEach(f=>byTicker.set(f.ticker,f));
    const results=findings.filter(f=>!normalized || f.ticker.includes(normalized) || f.stage.includes(normalized) || (f.signals||[]).some(s=>s.includes(normalized)) || (f.catalyst_headline||"").toUpperCase().includes(normalized));
    for(const c of catalysts){if((!normalized || c.ticker.includes(normalized) || c.headline.toUpperCase().includes(normalized)) && byTicker.has(c.ticker)){const f=byTicker.get(c.ticker)!; if(!results.some(x=>x.id===f.id))results.push(f);}}
    for(const g of gainers){if((!normalized || g.symbol.includes(normalized)) && byTicker.has(g.symbol)){const f=byTicker.get(g.symbol)!; if(!results.some(x=>x.id===f.id))results.push(f);}}
    return results.slice(0,12);
  },[normalized,findings,catalysts,gainers]);
  if(!open)return null;
  return <div className="command-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><div className="command-palette"><div className="command-input-row"><IconSearch size={15}/><input autoFocus value={query} onChange={e=>onQuery(e.target.value)} onKeyDown={e=>{if(e.key==="Escape")onClose();}} placeholder="Ticker, signal, catalyst…"/><kbd>Esc</kbd></div><div className="command-results">{candidates.length?candidates.map(f=><button key={f.id} onClick={()=>{onSelect(f);onClose();}}><div className="flex items-center gap-2"><b>{f.ticker}</b><EventIcon event={f.stage}/>{(f.signals||[]).filter(s=>s!==f.stage).slice(0,2).map(s=><Badge key={s} data-tone={tone(s)}>{s}</Badge>)}</div><div className="ml-auto text-right"><div className="metric">{money(f.price)}</div><div className="text-[9px] scout-muted">{age(f.detected_at)}</div></div></button>):<EmptyPane text="No matching Scout finding"/>}</div></div></div>;
}

function OpportunitySpotlight({items,onOpen,onStatus}:{items:AttentionItem[];onOpen:(item:AttentionItem)=>void;onStatus:(item:AttentionItem,status:AttentionStatus)=>void}){
  const queue=items.filter(item=>item.status==='unread').sort((a,b)=>b.priority-a.priority||b.updated_at-a.updated_at);
  const item=queue[0];
  if(!item)return null;
  const f=item.finding;
  return <aside className="opportunity-spotlight" aria-live="polite"><div className="spotlight-kicker"><IconBolt size={14}/> OPPORTUNITY <span>{queue.length} new</span></div><div className="spotlight-title"><b>{f.ticker}</b><EventIcon event={f.stage}/>{f.leg_context&&<Badge data-tone="green">{f.leg_context.replaceAll('_',' ')}</Badge>}<time>{age(item.updated_at)}</time></div><div className="spotlight-price">{money(f.price)} <span>{pct(f.change_15s_pct,1)} / 15s</span></div><div className="spotlight-evidence">RVOL {f.vol_ratio_15s?.toFixed(1)??'—'}× · {compactMoney(f.dollar_volume_15s)} · {f.trades_15s??'—'} trades</div><div className="spotlight-actions"><Button onClick={()=>onOpen(item)}>Open chart</Button><Button variant="ghost" onClick={()=>onStatus(item,'watching')}>Watch</Button><Button variant="ghost" onClick={()=>onStatus(item,'dismissed')}>Dismiss</Button></div><div className="spotlight-rule">Charts and Inspector stay unchanged until you open it.</div></aside>;
}

function DesktopWorkbench(props:WorkbenchProps) {
  const { findings,gainers,halts,catalysts,validation,timeline,selected,setSelected,status,connected,openNotifications,openCommand,scanner,saveScanner,scannerBusy,scannerMessage,attention,setAttentionStatus }=props;
  const [activity,setActivity]=useState<ActivityView>("radar");
  const [showPrimary,setShowPrimary]=useState(true);
  const [showInspector,setShowInspector]=useState(true);
  const [dockOpen,setDockOpen]=useState(true);
  const [dockMax,setDockMax]=useState(false);
  const [dockTab,setDockTab]=useState<DockTab>("catalysts");
  const connectionLabel=connected?"LIVE":API_CONFIGURED?"OFFLINE":"DEMO";
  const feedClass=(value:boolean|null|undefined)=>value===true?"feed-ok":value===false?"feed-bad":"feed-idle";
  const railCounts={radar:findings.filter(f=>f.quality_label==="CLEAN"&&f.stage!=="ACTIVITY_WATCH").length,ross:findings.filter(f=>f.ross_match).length,catalysts:catalysts.length,gainers:gainers.length,halts:halts.length,validation:validation.length,alerts:attention.filter(item=>!["dismissed","expired","acknowledged"].includes(item.status)).length};

  return <div className="desktop-workbench h-screen min-h-[680px] overflow-hidden">
    <header className="titlebar">
      <div className="titlebar-left"><div className="title-brand"><IconTargetArrow size={15}/><b>SCOUT</b><span className="version-chip">v{CLIENT_VERSION}</span></div><Badge data-tone={connected?"green":"orange"}><span className="live-dot"/>{connectionLabel}</Badge>{status?.replay?.active&&<Badge data-tone="orange">SIMULATION</Badge>}{!status?.replay?.active&&status?.replay?.latest_run&&<ScoutTooltip content={`Replay ${status.replay.latest_run.run_id}`}><span><Badge data-tone="blue">REPLAY READY</Badge></span></ScoutTooltip>}{status?.replay?.latest_run?.calibration&&<ScoutTooltip content={`${status.replay.latest_run.calibration.successful_precursors}/${status.replay.latest_run.calibration.precursors} shadow precursors expanded · ${status.replay.latest_run.calibration.missed_expansions} missed expansions`}><span><Badge data-tone="orange">CALIBRATED {status.replay.latest_run.calibration.precursors}</Badge></span></ScoutTooltip>}{status?.version&&status.version!==CLIENT_VERSION&&<Badge data-tone="red">VERSION MISMATCH</Badge>}{status?.hybrid?.rust_bridge?.enabled&&<Badge data-tone={status.hybrid.rust_bridge.running?"green":"red"}>{status.hybrid.rust_bridge.running?"HYBRID LIVE":"RUST DEGRADED"}</Badge>}<button className="session-button">All sessions <IconChevronDown size={12}/></button></div>
      <button className="command-center" aria-label="Search Scout" onClick={openCommand}><IconSearch size={13}/><span>Search ticker, catalyst, command…</span><kbd>Ctrl K</kbd></button>
      <div className="titlebar-right"><span>{status?.universe ?? "—"} symbols</span><span className={feedClass(status?.feeds.sip)}>SIP ●</span><span className={feedClass(status?.feeds.boats)}>BOATS ●</span><span className={feedClass(status?.feeds.news)}>NEWS ●</span><IconButton label="Toggle primary sidebar" active={showPrimary} onClick={()=>setShowPrimary(v=>!v)}><IconLayoutSidebarLeftCollapse size={15}/></IconButton><IconButton label="Toggle bottom panel" active={dockOpen} onClick={()=>setDockOpen(v=>!v)}><IconLayoutBottombarExpand size={15}/></IconButton><IconButton label="Toggle inspector" active={showInspector} onClick={()=>setShowInspector(v=>!v)}><IconLayoutSidebarRightCollapse size={15}/></IconButton><IconButton label="Notifications" onClick={openNotifications}><IconBellFilled size={14}/></IconButton></div>
    </header>

    <div className="workbench-canvas">
      <ActivityRail active={activity} onActive={setActivity} counts={railCounts}/>
      <div className="min-w-0 flex-1">
        <Group orientation="horizontal" className="h-full">
          {showPrimary && <><Panel id="primary" defaultSize="268px" minSize="220px" maxSize="390px"><div className="surface-wrap surface-primary"><div className="workbench-surface"><PrimarySidebar view={activity} findings={findings} gainers={gainers} halts={halts} catalysts={catalysts} validation={validation} selected={selected} onSelect={setSelected} connected={connected} onNotifications={openNotifications} scanner={scanner} saveScanner={saveScanner} scannerBusy={scannerBusy} scannerMessage={scannerMessage} attention={attention} setAttentionStatus={setAttentionStatus} backendVersion={status?.version}/></div></div></Panel><Separator className="workbench-gutter workbench-gutter-v"/></>}
          <Panel id="main" minSize="420px"><div className="surface-wrap surface-main"><div className="workbench-surface overflow-hidden">
            {dockMax && dockOpen ? <BottomDock tab={dockTab} setTab={setDockTab} catalysts={catalysts} findings={findings} selected={selected} status={status} validation={validation} timeline={timeline} onSelect={setSelected} onCollapse={()=>{setDockOpen(false);setDockMax(false)}} onMaximize={()=>setDockMax(false)} maximized/> : <Group orientation="vertical" className="h-full"><Panel id="workspace" minSize="260px"><ChartWorkspace findings={findings} selected={selected} onSelect={setSelected}/></Panel>{dockOpen && <><Separator className="workbench-gutter workbench-gutter-h"/><Panel id="dock" defaultSize="205px" minSize="120px" maxSize="45%"><div className="h-full pt-[3px]"><div className="dock-surface"><BottomDock tab={dockTab} setTab={setDockTab} catalysts={catalysts} findings={findings} selected={selected} status={status} validation={validation} timeline={timeline} onSelect={setSelected} onCollapse={()=>setDockOpen(false)} onMaximize={()=>setDockMax(true)} maximized={false}/></div></div></Panel></>}</Group>}
          </div>{!dockOpen && <button className="dock-collapsed" onClick={()=>setDockOpen(true)}><span>Catalysts</span><span>Evidence</span><span>Validation</span><span>Events</span><IconChevronUp size={13}/></button>}</div></Panel>
          {showInspector && <><Separator className="workbench-gutter workbench-gutter-v"/><Panel id="inspector" defaultSize="300px" minSize="250px" maxSize="420px"><div className="surface-wrap surface-inspector"><div className="workbench-surface"><PanelTitle icon={<IconTargetArrow size={13}/>} title="INSPECTOR" actions={<IconButton label="Close inspector" onClick={()=>setShowInspector(false)}><IconX size={13}/></IconButton>}/><div className="h-[calc(100%-34px)]"><Inspector finding={selected} onNotifications={openNotifications}/></div></div></div></Panel></>}
        </Group>
      </div>
    </div>
    <OpportunitySpotlight items={attention} onOpen={item=>{setSelected(item.finding);void setAttentionStatus(item,'opened');}} onStatus={(item,next)=>void setAttentionStatus(item,next)}/>
  </div>;
}

function MobileConsole(props:WorkbenchProps) {
  const {findings,gainers,halts,catalysts,selected,setSelected,status,connected,openNotifications,openCommand,scanner,saveScanner,scannerBusy,scannerMessage,attention,setAttentionStatus}=props;
  const [view,setView]=useState<MobileView>("radar");
  const [marketTab,setMarketTab]=useState<MarketTab>("radar");
  const connectionLabel=connected?"LIVE":API_CONFIGURED?"OFFLINE":"DEMO";
  const allFeedsLive=status?.feeds.sip===true&&status?.feeds.boats!==false&&status?.feeds.news===true;
  const marketIcons:{id:MarketTab;label:string;icon:React.ReactNode}[]=[{id:"radar",label:"Radar",icon:<IconBolt size={20}/>},{id:"gainers",label:"Top gainers",icon:<IconTrendingUp size={20}/>},{id:"halted",label:"Halted",icon:<IconPlayerPauseFilled size={17}/>}];
  const nav:{id:MobileView;label:string;icon:React.ReactNode}[]=[{id:"radar",label:"Radar",icon:<IconBolt size={22}/>},{id:"charts",label:"Charts",icon:<IconChartBar size={22}/>},{id:"catalysts",label:"Catalysts",icon:<IconDiamondFilled size={18}/>},{id:"alerts",label:"Notifications",icon:<IconBellFilled size={18}/>},{id:"settings",label:"Settings",icon:<IconSettings size={21}/>}];
  return <div className="mobile-console mobile-safe min-h-screen">
    <header className="mobile-header"><div className="flex h-12 items-center justify-between px-3"><div className="flex items-center gap-2"><IconTargetArrow size={17}/><b className="tracking-[.08em]">SCOUT</b><span className="version-chip">v{CLIENT_VERSION}</span><Badge data-tone={connected?"green":"orange"}><span className="live-dot"/>{connectionLabel}</Badge>{status?.replay?.active&&<Badge data-tone="orange">SIMULATION</Badge>}{!status?.replay?.active&&status?.replay?.latest_run&&<Badge data-tone="blue">REPLAY READY</Badge>}</div><div className="flex items-center gap-1"><IconButton label="Search" onClick={openCommand}><IconSearch size={18}/></IconButton><IconButton label="Notifications" onClick={openNotifications}><IconBellFilled size={16}/></IconButton></div></div><div className="mobile-status"><span>All sessions · {status?.universe ?? "—"}</span><span className={allFeedsLive?"feed-ok":status?"feed-bad":"feed-idle"}>SIP ● BOATS ● NEWS ●</span></div></header>
    <main className="pb-20">
      {view==="radar" && <><div className="mobile-market-tabs">{marketIcons.map(item=><button key={item.id} aria-label={item.label} data-active={marketTab===item.id || undefined} onClick={()=>setMarketTab(item.id)}>{item.icon}{item.id==="halted"&&halts.length?<span className="mobile-dot-count">{halts.length}</span>:null}</button>)}</div>{marketTab==="radar"&&findings.map(f=><FindingRow key={f.id} finding={f} selected={selected?.id===f.id} onSelect={()=>{setSelected(f);setView("charts");}}/>)}{marketTab==="gainers"&&<GainerRows gainers={gainers} findings={findings} onSelect={finding=>{setSelected(finding);setView("charts");}}/>}{marketTab==="halted"&&<HaltRows halts={halts} findings={findings} onSelect={finding=>{setSelected(finding);setView("charts");}}/>}</>}
      {view==="charts" && <div className="mobile-chart-page">{selected?<><div className="mobile-page-title"><div><b>{selected.ticker}</b><span className="metric ml-2">{money(selected.price)}</span></div><div className="flex gap-1">{Array.from(new Set([selected.stage,...(selected.signals||[])])).slice(0,3).map(signal=><EventIcon key={signal} event={signal}/>)}</div></div><div className="mobile-live-chart"><LiveChart finding={selected} onSelectFinding={setSelected}/></div><div className="mobile-inspector"><Inspector finding={selected} onNotifications={openNotifications}/></div></>:<EmptyPane text="Select a ticker from Radar"/>}</div>}
      {view==="catalysts" && <div className="mobile-page"><PanelTitle icon={<IconDiamondFilled size={12}/>} title="CATALYSTS"/><CatalystList catalysts={catalysts} findings={findings} onSelect={finding=>{setSelected(finding);setView("charts");}}/></div>}
      {view==="alerts" && <div className="mobile-page"><AttentionInbox items={attention} onOpen={item=>{setSelected(item.finding);void setAttentionStatus(item,'opened');setView('charts');}} onStatus={(item,next)=>void setAttentionStatus(item,next)}/></div>}
      {view==="settings" && <div className="mobile-page"><SettingsPanel connected={connected} onNotifications={openNotifications} scanner={scanner} saveScanner={saveScanner} scannerBusy={scannerBusy} scannerMessage={scannerMessage} backendVersion={status?.version}/></div>}
    </main>
    <nav className="mobile-bottom-nav">{nav.map(item=><button key={item.id} aria-label={item.label} data-active={view===item.id || undefined} onClick={()=>setView(item.id)}>{item.icon}</button>)}</nav>
    <OpportunitySpotlight items={attention} onOpen={item=>{setSelected(item.finding);void setAttentionStatus(item,'opened');setView('charts');}} onStatus={(item,next)=>void setAttentionStatus(item,next)}/>
  </div>;
}

export default function ScoutPage() {
  const [findings,setFindings]=useState<Finding[]>(API_CONFIGURED?[]:demoFindings);
  const [catalysts,setCatalysts]=useState<Catalyst[]>(API_CONFIGURED?[]:demoCatalysts);
  const [gainers,setGainers]=useState<Gainer[]>(API_CONFIGURED?[]:demoGainers);
  const [halts,setHalts]=useState<Halt[]>(API_CONFIGURED?[]:demoHalts);
  const [validation,setValidation]=useState<ValidationRow[]>(API_CONFIGURED?[]:demoValidation);
  const [timeline,setTimeline]=useState<TimelineItem[]>(API_CONFIGURED?[]:demoTimeline);
  const [status,setStatus]=useState<ScoutStatus|null>(null);
  const [selected,setSelectedState]=useState<Finding|undefined>(API_CONFIGURED?undefined:demoFindings[0]);
  const [prefs,setPrefs]=useState<NotificationPreferences>(defaultPrefs);
  const [connected,setConnected]=useState(false);
  const [notificationOpen,setNotificationOpen]=useState(false);
  const [saving,setSaving]=useState(false);
  const [testMessage,setTestMessage]=useState("");
  const [commandOpen,setCommandOpen]=useState(false);
  const [commandQuery,setCommandQuery]=useState("");
  const [scanner,setScanner]=useState<ScannerSettings>({min_price:.15,max_price:10});
  const [scannerBusy,setScannerBusy]=useState(false);
  const [scannerMessage,setScannerMessage]=useState("");
  const [attention,setAttention]=useState<AttentionItem[]>([]);

  const setSelected=useCallback((finding:Finding)=>{
    setSelectedState(finding);
    if(API_CONFIGURED)prefetchMarketSnapshot(finding.ticker,finding.detected_at,15,finding.id);
    if(typeof window!=="undefined"){
      const url=new URL(window.location.href);
      url.searchParams.set("finding",String(finding.id));
      url.searchParams.set("ticker",finding.ticker);
      window.history.replaceState({},"",`${url.pathname}${url.search}`);
    }
  },[]);

  const applyDeepLink=useCallback(async(rawUrl?:string)=>{
    if(typeof window==="undefined")return;
    let params:URLSearchParams;
    try {
      const source=rawUrl ? new URL(rawUrl) : new URL(window.location.href);
      params=source.searchParams;
    } catch { params=new URLSearchParams(window.location.search); }
    const id=Number(params.get("finding")||0);
    const ticker=(params.get("ticker")||"").toUpperCase();
    if(id){
      const local=findings.find(f=>f.id===id);
      if(local){setSelectedState(local);return;}
      if(API_CONFIGURED){try{setSelectedState(await getFinding(id));return;}catch{}}
    }
    if(ticker){const local=findings.find(f=>f.ticker===ticker);if(local)setSelectedState(local);}
  },[findings]);

  const refresh=useCallback(async(heavy=true)=>{
    if (!API_CONFIGURED) return;
    const results = await Promise.allSettled([
      getStatus(),getFindings(300),getCatalysts(120),getHalts(),getScannerSettings(),getAttention(120),
    ]);
    const [statusResult,findingsResult,catalystsResult,haltsResult,scannerResult,attentionResult]=results;
    let anySuccess=false;
    if(statusResult.status==='fulfilled'){setStatus(statusResult.value);anySuccess=true;}
    if(findingsResult.status==='fulfilled'){
      const fresh=findingsResult.value;setFindings(fresh);anySuccess=true;
      setSelectedState(current=>current?fresh.find(f=>f.id===current.id)??fresh.find(f=>f.ticker===current.ticker)??current:current);
    }
    if(catalystsResult.status==='fulfilled')setCatalysts(catalystsResult.value);
    if(haltsResult.status==='fulfilled')setHalts(haltsResult.value.active);
    if(scannerResult.status==='fulfilled')setScanner(scannerResult.value);
    if(attentionResult.status==='fulfilled')setAttention(attentionResult.value);
    if(heavy){
      const extra=await Promise.allSettled([getGainers(30),getNotificationPreferences(),getValidation(120),getTimeline(undefined,160)]);
      const [gainersResult,prefsResult,validationResult,timelineResult]=extra;
      if(gainersResult.status==='fulfilled')setGainers(gainersResult.value);
      if(prefsResult.status==='fulfilled'){setPrefs(prefsResult.value);}
      if(validationResult.status==='fulfilled')setValidation(validationResult.value);
      if(timelineResult.status==='fulfilled')setTimeline(timelineResult.value);
    }
    setConnected(anySuccess);
  },[]);

  useEffect(()=>{void refresh(true);const core=window.setInterval(()=>void refresh(false),30000);const heavy=window.setInterval(()=>void refresh(true),120000);return()=>{window.clearInterval(core);window.clearInterval(heavy);};},[refresh]);
  useEffect(()=>{applyUiPreferences(readUiPreferences());},[]);
  useEffect(()=>{const count=attention.filter(item=>item.status==='unread').length;const nav=navigator as Navigator&{setAppBadge?:(value?:number)=>Promise<void>;clearAppBadge?:()=>Promise<void>};if(count)void nav.setAppBadge?.(count);else void nav.clearAppBadge?.();},[attention]);

  useEffect(()=>{
    void applyDeepLink();
    if(typeof window==='undefined')return;
    const handler=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();setCommandOpen(true);}if(event.key==='Escape')setCommandOpen(false);};
    window.addEventListener('keydown',handler);
    return()=>window.removeEventListener('keydown',handler);
  },[applyDeepLink]);

  useEffect(()=>installNativeDeepLinkHandler(url=>{void applyDeepLink(url);}),[applyDeepLink]);
  useEffect(()=>installNativeNotificationActionHandler(url=>{void applyDeepLink(url);}),[applyDeepLink]);

  useEffect(()=>{
    if(typeof window==='undefined'||!API_CONFIGURED)return;
    const source=new EventSource(`${API_BASE}/api/events`);
    source.addEventListener('ready',()=>setConnected(true));
    source.addEventListener('finding',(event)=>{
      try{
        const envelope=JSON.parse((event as MessageEvent).data);
        if(envelope?.payload){
          const finding=envelope.payload as Finding;
          setFindings(current=>[finding,...current.filter(f=>f.ticker!==finding.ticker)].slice(0,300));
          setSelectedState(current=>current?.ticker===finding.ticker?finding:current);
        }
      }catch{}
      void refresh(false);
    });
    source.addEventListener('chart',()=>void refresh(false));
    ['halt','resume'].forEach(name=>source.addEventListener(name,()=>void refresh(false)));
    source.addEventListener('notification-preferences',()=>void refresh(true));
    source.addEventListener('scanner-settings',()=>void refresh(true));
    source.addEventListener('attention',()=>void refresh(false));
    source.onerror=()=>setConnected(false);
    return()=>source.close();
  },[refresh,prefs]);

  async function savePrefs(){
    if(!API_CONFIGURED){setNotificationOpen(false);return;}
    setSaving(true);setTestMessage("");
    try{const saved=await saveNotificationPreferences(prefs);setPrefs(saved);setNotificationOpen(false);}catch(error){setTestMessage(error instanceof Error?error.message:"Unable to save preferences");}finally{setSaving(false);}
  }

  async function runNotificationTest(platform:"windows"|"android"|"email"){
    setTestMessage(`Testing ${platform}…`);
    try{
      if(platform!=="email"){
        const native=await sendNativeTestNotification(platform);
        if(native){setTestMessage(`${platform[0].toUpperCase()+platform.slice(1)} native test sent.`);return;}
      }
      if(!API_CONFIGURED){setTestMessage("Connect Scout API to test this notification channel.");return;}
      const result=await testNotification(platform);setTestMessage(result.message);
    }catch(error){setTestMessage(error instanceof Error?error.message:'Notification test failed');}
  }

  async function applyScannerRange(value:ScannerSettings){
    setScannerBusy(true);setScannerMessage("");
    try{const saved=API_CONFIGURED?await saveScannerSettings(value):value;setScanner(saved);setScannerMessage(`Scanner now tracks $${saved.min_price.toFixed(2)}–$${saved.max_price.toFixed(2)}.`);void refresh(true);}catch(error){setScannerMessage(error instanceof Error?error.message:"Unable to update scanner range");}finally{setScannerBusy(false);}
  }

  async function setAttentionStatus(item:AttentionItem,next:AttentionStatus){
    setAttention(current=>current.map(row=>row.id===item.id?{...row,status:next,updated_at:Math.floor(Date.now()/1000)}:row));
    if(!API_CONFIGURED)return;
    try{const saved=await updateAttention(item.id,next);setAttention(current=>current.map(row=>row.id===saved.id?saved:row));}catch{void refresh(false);}
  }

  const inRange=useCallback((price?:number|null)=>price!=null&&price>=scanner.min_price&&price<=scanner.max_price,[scanner]);
  const visibleFindings=useMemo(()=>findings.filter(f=>inRange(f.price)),[findings,inRange]);
  const visibleGainers=useMemo(()=>gainers.filter(g=>g.price==null||inRange(g.price)),[gainers,inRange]);

  const props:WorkbenchProps={
    findings:visibleFindings,gainers:visibleGainers,halts,catalysts,validation,timeline,selected,setSelected,status,connected,
    openNotifications:()=>setNotificationOpen(true),openCommand:()=>setCommandOpen(true),
    scanner,saveScanner:applyScannerRange,scannerBusy,scannerMessage,attention,setAttentionStatus,
  };

  return <TooltipProvider><div className="scout-shell">
    <PwaRuntime/>
    <DesktopWorkbench {...props}/>
    <MobileConsole {...props}/>
    <NotificationSheet open={notificationOpen} prefs={prefs} status={status} onClose={()=>setNotificationOpen(false)} onChange={setPrefs} onSave={savePrefs} onTest={runNotificationTest} saving={saving} testMessage={testMessage}/>
    <CommandPalette open={commandOpen} query={commandQuery} onQuery={setCommandQuery} findings={visibleFindings} catalysts={catalysts} gainers={visibleGainers} onClose={()=>{setCommandOpen(false);setCommandQuery("");}} onSelect={setSelected}/>
  </div></TooltipProvider>;
}
