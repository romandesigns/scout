"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {IconArrowLeft,IconBug,IconChartCandle,IconFlask,IconRefresh,IconReportAnalytics,IconZoomIn} from "@tabler/icons-react";
import {API_BASE,getDevelopmentEvaluations,getStatus,runDevelopmentEvaluations} from "@/lib/api";
import type {DevelopmentEvaluation,ScoutStatus} from "@/lib/types";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Badge} from "@/components/ui/badge";
import {ChartAnnotationEditor} from "@/components/chart-annotation-editor";

type Tab="chart-review"|"insights"|"debugging"|"testing";
const INSPECTION_RANGE_STORAGE_KEY="scout-development-inspection-range-v1";
const tabs:{id:Tab;label:string;icon:React.ReactNode}[]=[
  {id:"chart-review",label:"Chart review",icon:<IconChartCandle size={16}/>},
  {id:"insights",label:"Insights",icon:<IconReportAnalytics size={16}/>},
  {id:"debugging",label:"Debugging",icon:<IconBug size={16}/>},
  {id:"testing",label:"Testing",icon:<IconFlask size={16}/>},
];
const tone=(value?:string)=>value==="WINNER"?"green":value==="FAILED"||value==="NO_EDGE"?"red":value==="PARTIAL"?"orange":"blue";
const num=(value?:number|null,digits=2)=>value==null?"—":value.toFixed(digits);

export default function DevelopmentPage(){
  const [inspectionRangeLoaded,setInspectionRangeLoaded]=useState(false);
  const [tab,setTab]=useState<Tab>("chart-review");
  const [tickers,setTickers]=useState("");
  const [timeframe,setTimeframe]=useState<30|60|300>(60);
  const [useLatest,setUseLatest]=useState(true);
  const [when,setWhen]=useState("");
  const [inspectRange,setInspectRange]=useState(false);
  const [rangeStart,setRangeStart]=useState("");
  const [rangeEnd,setRangeEnd]=useState("");
  const [useLiveDetector,setUseLiveDetector]=useState(false);
  const [detectorEngine,setDetectorEngine]=useState<"python"|"rust"|"both">("rust");
  const [items,setItems]=useState<DevelopmentEvaluation[]>([]);
  const [status,setStatus]=useState<ScoutStatus|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [expandedChart,setExpandedChart]=useState<{evaluationId:number;ticker:string;src:string;alt:string}|null>(null);
  useEffect(()=>{
    try{
      const saved=localStorage.getItem(INSPECTION_RANGE_STORAGE_KEY);
      if(saved){
        const value=JSON.parse(saved) as {enabled?:boolean;start?:string;end?:string;liveDetector?:boolean;detectorEngine?:"python"|"rust"|"both"};
        setInspectRange(Boolean(value.enabled));setRangeStart(value.start||"");setRangeEnd(value.end||"");
        setUseLiveDetector(Boolean(value.liveDetector));
        setDetectorEngine(value.detectorEngine||"rust");
      }
    }catch{/* Ignore unavailable or malformed device storage. */}
    setInspectionRangeLoaded(true);
  },[]);
  useEffect(()=>{
    if(!inspectionRangeLoaded)return;
    try{localStorage.setItem(INSPECTION_RANGE_STORAGE_KEY,JSON.stringify({enabled:inspectRange,start:rangeStart,end:rangeEnd,liveDetector:useLiveDetector,detectorEngine}));}catch{/* The form still works when storage is unavailable. */}
  },[inspectionRangeLoaded,inspectRange,rangeStart,rangeEnd,useLiveDetector,detectorEngine]);
  const refresh=useCallback(async()=>{try{const [rows,state]=await Promise.all([getDevelopmentEvaluations(200),getStatus()]);setItems(rows);setStatus(state);}catch(error){setMessage(String(error));}},[]);
  useEffect(()=>{void refresh();},[refresh]);
  const run=async()=>{
    const symbols=[...new Set(tickers.toUpperCase().split(/[\s,]+/).filter(Boolean))];
    if(!symbols.length){setMessage("Enter at least one ticker.");return;}
    const liveDetectorActive=inspectRange&&useLiveDetector;
    setBusy(true);setMessage(liveDetectorActive
      ?`Running Scout's live detector for ${symbols.length} ticker${symbols.length===1?"":"s"} against real Alpaca trades (slower)…`
      :`Evaluating ${symbols.length} ticker${symbols.length===1?"":"s"} against Alpaca data…`);
    try{
      const detection_at=!useLatest&&when?new Date(when).getTime()/1000:undefined;
      if(inspectRange&&(!rangeStart||!rangeEnd)){setMessage("Choose both the inspection start and end time.");setBusy(false);return;}
      const inspection_start=inspectRange?new Date(rangeStart).getTime()/1000:undefined;
      const inspection_end=inspectRange?new Date(rangeEnd).getTime()/1000:undefined;
      if(inspectRange&&Number(inspection_end)<=Number(inspection_start)){setMessage("Inspection end must be after its start.");setBusy(false);return;}
      if(liveDetectorActive&&Number(inspection_end)-Number(inspection_start)>4*3600){setMessage("Live detector replay is capped at a 4-hour window (tick-level replay is slower). Narrow the section or turn it off.");setBusy(false);return;}
      const results=await runDevelopmentEvaluations({tickers:symbols,timeframe_seconds:timeframe,detection_at,use_latest_finding:useLatest,inspection_start,inspection_end,use_live_detector:liveDetectorActive,detector_engine:detectorEngine});
      setItems(current=>[...results,...current]);
      const failures=results.filter(item=>item.status==="error").length;
      setMessage(`Completed ${results.length-failures}/${results.length}${failures?` · ${failures} failed`:""}.`);
    }catch(error){setMessage(String(error));}finally{setBusy(false);}
  };
  const complete=items.filter(item=>item.status==="complete");
  const mature=complete.filter(item=>item.metrics.verdict!=="PENDING");
  const summary=useMemo(()=>({
    total:complete.length,winners:mature.filter(item=>item.metrics.verdict==="WINNER").length,
    failed:mature.filter(item=>["FAILED","NO_EDGE"].includes(item.metrics.verdict||"")).length,
    avgR:mature.length?mature.reduce((sum,item)=>sum+(item.metrics.max_favorable_r||0),0)/mature.length:0,
    hit3:mature.length?mature.filter(item=>item.metrics.hit_3r).length/mature.length*100:0,
  }),[complete,mature]);
  return <main className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
    <header className="flex h-14 items-center justify-between border-b border-[var(--border)] px-5">
      <div className="flex items-center gap-3"><a href="/" className="rounded-md p-2 hover:bg-[var(--muted)]" title="Back to Scout"><IconArrowLeft size={18}/></a><IconFlask className="text-[var(--blue)]" size={20}/><div><b>SCOUT DEVELOPMENT</b><div className="text-[10px] scout-muted">Testing · debugging · insights · reproducible formation audits</div></div></div>
      <div className="flex items-center gap-2"><Badge data-tone={status?"green":"red"}>{status?`BACKEND ${status.version}`:"OFFLINE"}</Badge><Button variant="ghost" onClick={()=>void refresh()}><IconRefresh size={15}/> Refresh</Button></div>
    </header>
    <nav className="flex gap-1 border-b border-[var(--border)] px-5 py-2">{tabs.map(item=><Button key={item.id} variant={tab===item.id?"default":"ghost"} onClick={()=>setTab(item.id)}>{item.icon}{item.label}</Button>)}</nav>
    <section className="mx-auto max-w-[1680px] p-5">
      {tab==="chart-review"&&<div className="grid gap-4 xl:grid-cols-[390px_1fr]">
        <aside className="space-y-4 rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4">
          <div><h2 className="font-semibold">Formation evaluator</h2><p className="mt-1 text-xs scout-muted">Enter tickers and reconstruct standardized charts from Alpaca market data around Scout&apos;s original detection.</p></div>
          <label className="block text-xs">Tickers<Input className="mt-1" value={tickers} onChange={event=>setTickers(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!busy){event.preventDefault();void run();}}} placeholder="PACB, IVVD, GOSS"/></label>
          <div className="grid grid-cols-3 gap-2">{([30,60,300] as const).map(value=><Button key={value} variant={timeframe===value?"default":"ghost"} onClick={()=>setTimeframe(value)}>{value===30?"30 sec":value===60?"1 min":"5 min"}</Button>)}</div>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={useLatest} onChange={event=>setUseLatest(event.target.checked)}/>Use each ticker&apos;s latest Scout detection</label>
          {!useLatest&&<label className="block text-xs">Detection time<Input className="mt-1" type="datetime-local" value={when} onChange={event=>setWhen(event.target.value)}/></label>}
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={inspectRange} onChange={event=>setInspectRange(event.target.checked)}/>Inspect a specific chart section</label>
          {inspectRange&&<div className="grid gap-2"><label className="block text-xs">Section start<Input className="mt-1" type="datetime-local" value={rangeStart} onChange={event=>setRangeStart(event.target.value)}/></label><label className="block text-xs">Section end<Input className="mt-1" type="datetime-local" value={rangeEnd} onChange={event=>setRangeEnd(event.target.value)}/></label><div className="text-[10px] scout-muted">Up to 24 hours. When detection matching is enabled, Scout selects the latest detection inside this range.</div>
            <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5" checked={useLiveDetector} onChange={event=>setUseLiveDetector(event.target.checked)}/><span>Run Scout&apos;s live detector over this window <span className="scout-muted">(slower — replays real tick data through the actual production detector instead of looking up stored detections; answers &quot;what would Scout have flagged here&quot; for a ticker/date Scout never actually watched. Capped at 4 hours.)</span></span></label>
            {useLiveDetector&&<label className="block text-xs">Replay engine<select className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-2" value={detectorEngine} onChange={event=>setDetectorEngine(event.target.value as "python"|"rust"|"both")}><option value="rust">Rust recipes (recommended)</option><option value="both">Rust + Python comparison</option><option value="python">Python detector only</option></select></label>}
          </div>}
          <Button className="w-full" disabled={busy} onClick={()=>void run()}>{busy?(inspectRange&&useLiveDetector?"Running live detector…":"Building audits…"):(inspectRange&&useLiveDetector?"Run Scout's live detector":"Run Alpaca chart audit")}</Button>
          {message&&<div className="rounded-md border border-[var(--border)] p-3 text-xs scout-muted">{message}</div>}
          <div className="rounded-md bg-[var(--muted)] p-3 text-xs"><b>What is captured</b><ul className="mt-2 space-y-1 scout-muted"><li>By default: every Scout detection already <b>stored</b> in the visible range</li><li>With the live detector on: every detection Scout&apos;s <b>real production engine finds right now</b>, replaying that window&apos;s actual trades — for a ticker/date Scout never watched live, nothing is stored either way</li><li>Significance tier: structural breakout, continuation pulse, or reaction bounce</li><li>Where Scout&apos;s notification gate would have fired (preview)</li><li><b>Real bullish momentum zones</b> — computed straight from price/volume, independent of Scout — shaded green if a Tier 1/2 or would-notify detection caught it inside its lead window, orange if not, so you can see detector accuracy directly on the chart</li><li>15–30 second target area after each detection</li><li>Shadow ML gate pass, reject, or unscored status</li><li>Trigger and invalidation</li><li>30s, 1m, 5m and 15m excursion</li><li>Maximum favorable/adverse R</li></ul></div>
        </aside>
        <div className="space-y-4">
          {!items.length&&<div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-[var(--border)] scout-muted">Run an audit to create the first chart review.</div>}
          {items.map(item=><article key={item.id} className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)]">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] p-3"><div className="flex items-center gap-2"><b>{item.ticker}</b><Badge data-tone={tone(item.metrics.verdict)}>{item.status==="error"?"ERROR":item.metrics.verdict||"COMPLETE"}</Badge>{item.metrics.use_live_detector&&<Badge data-tone="blue">LIVE DETECTOR REPLAY</Badge>}<span className="text-xs scout-muted">{item.timeframe_seconds}s · {new Date(item.detection_at*1000).toLocaleString()}</span></div>{item.finding_id&&<a className="text-xs text-[var(--blue)]" href={`/?ticker=${item.ticker}&finding=${item.finding_id}`}>Open Scout detection →</a>}</div>
            {item.status==="error"?<div className="p-5 text-sm text-[var(--red)]">{item.error}</div>:<div className="grid xl:grid-cols-[1fr_250px]">
              <div>{item.metrics.detection_note&&<div className="border-b border-[var(--border)] px-3 py-2 text-xs scout-muted">{item.metrics.detection_note}</div>}{item.chart_url&&<button type="button" className="group relative block w-full cursor-zoom-in" onClick={()=>setExpandedChart({evaluationId:item.id,ticker:item.ticker,src:`${API_BASE}${item.chart_url}`,alt:`${item.ticker} formation audit with marked detections`})} aria-label={`Enlarge and annotate ${item.ticker} formation audit chart`}><img src={`${API_BASE}${item.chart_url}`} alt={`${item.ticker} formation audit with marked detections`} className="w-full bg-white/5"/><span className="absolute right-3 top-3 flex items-center gap-1 rounded-md bg-black/75 px-2 py-1 text-xs text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"><IconZoomIn size={15}/> Enlarge & annotate</span></button>}<div className="flex flex-wrap gap-3 border-t border-[var(--border)] px-3 py-2 text-[10px] scout-muted"><span><b className="text-[#ff5d73]">Red</b> Tier 1 breakout</span><span><b className="text-[#4aa8ff]">Blue</b> Tier 2 continuation</span><span><b className="text-[#6b7686]">Gray</b> Tier 3 bounce</span><span><b className="text-[#39d2c0]">Cyan star</b> would notify (preview)</span><span><b className="text-[#2ed6a1]">Green shading</b> real momentum, caught</span><span><b className="text-[#ffb020]">Orange shading</b> real momentum, missed</span><span>Shading = +15s to +30s area</span></div></div>
              <div className="grid grid-cols-2 gap-px bg-[var(--border)] text-xs xl:grid-cols-1">{[
                ["Detections marked",String(item.metrics.detections_marked||0)],["Rust evaluations",String(item.metrics.rust_evaluation_count||0)],["Rust rejected",String(item.metrics.rust_rejected_count||0)],["Tier 1 / 2 / 3",`${item.metrics.tier_counts?.tier_1||0} / ${item.metrics.tier_counts?.tier_2||0} / ${item.metrics.tier_counts?.tier_3||0}`],["Would notify (preview)",String(item.metrics.would_notify_preview_marked||0)],["Gate passes",String(item.metrics.gate_passes_marked||0)],
                ["Real momentum zones",String(item.metrics.momentum_zones_marked||0)],["Caught by Scout",`${item.metrics.momentum_zones_caught||0}/${item.metrics.momentum_zones_marked||0}`],["Detector recall",item.metrics.objective_zone_metrics?.recall_pct==null?"—":`${num(item.metrics.objective_zone_metrics.recall_pct,0)}%`],["Detector precision",item.metrics.objective_zone_metrics?.precision_pct==null?"—":`${num(item.metrics.objective_zone_metrics.precision_pct,0)}%`],["Median lead",item.metrics.objective_zone_metrics?.median_lead_seconds==null?"—":`${num(item.metrics.objective_zone_metrics.median_lead_seconds,0)}s`],
                ["Lifecycle",item.metrics.unified_evidence?.phase||"—"],["Supply family",item.metrics.unified_evidence?.supply==null?"—":`${num(item.metrics.unified_evidence.supply,0)}/100`],["Compression",item.metrics.unified_evidence?.compression_quality==null?"—":`${num(item.metrics.unified_evidence.compression_quality,0)}/100`],["Box quality",item.metrics.unified_evidence?.box?.quality==null?"—":`${num(item.metrics.unified_evidence.box.quality,0)}/100`],["Pullback quality",item.metrics.unified_evidence?.pullback?.quality==null?"—":`${num(item.metrics.unified_evidence.pullback.quality,0)}/100`],
                ["MFE",`${num(item.metrics.max_favorable_r)}R`],["MAE",`${num(item.metrics.max_adverse_r)}R`],["Capture efficiency",`${num(item.metrics.capture_efficiency_pct,1)}%`],["First touch",item.metrics.first_touch||"—"],["30 sec",`${num(item.metrics.max_30s_pct)}%`],["1 minute",`${num(item.metrics.max_1m_pct)}%`],["5 minutes",`${num(item.metrics.max_5m_pct)}%`],["15 minutes",`${num(item.metrics.max_15m_pct)}%`],["3R target",item.metrics.hit_3r?"Reached":"Not reached"],["Invalidation",item.metrics.invalidated?"Breached":"Held"],
              ].map(([label,value])=><div key={label} className="bg-[var(--panel)] p-3"><div className="scout-muted">{label}</div><b>{value}</b></div>)}</div>
            </div>}
          </article>)}
        </div>
      </div>}
      {tab==="insights"&&<div className="space-y-4"><div className="grid gap-3 md:grid-cols-5">{[["Audits",summary.total],["Mature",mature.length],["Winners",summary.winners],["3R hit rate",`${num(summary.hit3,1)}%`],["Average MFE",`${num(summary.avgR)}R`]].map(([label,value])=><div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4"><div className="text-xs scout-muted">{label}</div><div className="mt-2 text-2xl font-semibold">{value}</div></div>)}</div><InsightTable items={mature}/></div>}
      {tab==="debugging"&&<div className="grid gap-4 lg:grid-cols-2"><JsonCard title="Pipeline latency" value={status?.hybrid?.pipeline_latency}/><JsonCard title="Rust bridge" value={status?.hybrid?.rust_bridge}/><JsonCard title="Feed health" value={status?.feeds.health}/><JsonCard title="Notification delivery" value={status?.notifications}/></div>}
      {tab==="testing"&&<div className="grid gap-4 lg:grid-cols-3"><ToolCard title="Chart audit" text="Reconstruct a detection against Alpaca data and preserve a visual artifact." action={()=>setTab("chart-review")}/><ToolCard title="Pipeline diagnostics" text="Inspect Rust, feed, notification, and end-to-end latency health." action={()=>setTab("debugging")}/><ToolCard title="Outcome insights" text="Compare favorable R, invalidations, and 3R attainment across audited detections." action={()=>setTab("insights")}/></div>}
    </section>
    {expandedChart&&<ChartAnnotationEditor {...expandedChart} onClose={()=>setExpandedChart(null)}/>} 
  </main>;
}

function JsonCard({title,value}:{title:string;value:unknown}){return <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4"><h3 className="font-semibold">{title}</h3><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-3 text-[11px] text-[var(--muted-foreground)]">{JSON.stringify(value??{},null,2)}</pre></div>}
function ToolCard({title,text,action}:{title:string;text:string;action:()=>void}){return <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-5"><h3 className="font-semibold">{title}</h3><p className="my-3 text-xs scout-muted">{text}</p><Button onClick={action}>Open</Button></div>}
function InsightTable({items}:{items:DevelopmentEvaluation[]}){const grouped=Object.values(items.reduce<Record<string,{key:string,count:number,wins:number,r:number}>>((all,item)=>{const f=item.metrics.formation||{};const key=`${f.stage||"UNKNOWN"} · ${f.rank||"—"} · ${item.timeframe_seconds}s`;const row=all[key]||{key,count:0,wins:0,r:0};row.count++;row.wins+=item.metrics.verdict==="WINNER"?1:0;row.r+=item.metrics.max_favorable_r||0;all[key]=row;return all;},{}));return <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)]"><div className="border-b border-[var(--border)] p-4 font-semibold">Performance by Scout formation cohort</div><table className="w-full text-left text-xs"><thead className="scout-muted"><tr><th className="p-3">Cohort</th><th>Samples</th><th>Winner rate</th><th>Average MFE</th></tr></thead><tbody>{grouped.map(row=><tr key={row.key} className="border-t border-[var(--border)]"><td className="p-3">{row.key}</td><td>{row.count}</td><td>{num(row.wins/row.count*100,1)}%</td><td>{num(row.r/row.count)}R</td></tr>)}</tbody></table></div>}
