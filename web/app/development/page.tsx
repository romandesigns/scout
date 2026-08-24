"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {IconArrowLeft,IconBug,IconChartCandle,IconFlask,IconRefresh,IconReportAnalytics,IconZoomIn} from "@tabler/icons-react";
import {Bar,BarChart,CartesianGrid,Cell,PolarAngleAxis,PolarGrid,Radar,RadarChart,RadialBar,RadialBarChart,Tooltip as RechartsTooltip,XAxis,YAxis} from "recharts";
import {API_BASE,getDevelopmentEvaluations,getStatus,runDevelopmentEvaluations} from "@/lib/api";
import type {DevelopmentEvaluation,ScoutStatus} from "@/lib/types";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Badge} from "@/components/ui/badge";
import {ChartContainer,ChartTooltipContent} from "@/components/ui/chart";
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
        const hasSavedRange=Boolean(value.enabled&&value.start&&value.end);
        setInspectRange(hasSavedRange);setRangeStart(value.start||"");setRangeEnd(value.end||"");
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
  return <main className="scout-shell development-shell">
    <header className="development-titlebar">
      <div className="development-titlebar-left"><a href="/" className="icon-button" title="Back to Scout"><IconArrowLeft size={16}/></a><span className="development-brand-mark"><IconFlask size={15}/></span><div><b className="development-title">SCOUT DEVELOPMENT</b><div className="development-subtitle">Formation audits · detector validation · replay evidence</div></div></div>
      <div className="flex items-center gap-2"><Badge data-tone={status?"green":"red"}>{status?`BACKEND ${status.version}`:"OFFLINE"}</Badge><Button variant="ghost" onClick={()=>void refresh()}><IconRefresh size={15}/> Refresh</Button></div>
    </header>
    <nav className="development-tabs">{tabs.map(item=><Button key={item.id} variant={tab===item.id?"default":"ghost"} onClick={()=>setTab(item.id)}>{item.icon}{item.label}</Button>)}</nav>
    <section className="development-content">
      {tab==="chart-review"&&<div className="development-layout">
        <aside className="development-sidebar">
          <div><h2 className="font-semibold">Formation evaluator</h2><p className="mt-1 text-xs scout-muted">Enter tickers and reconstruct standardized charts from Alpaca market data around Scout&apos;s original detection.</p></div>
          <label className="development-control"><span className="development-control-label">Tickers</span><Input aria-describedby="ticker-help" value={tickers} onChange={event=>setTickers(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!busy){event.preventDefault();void run();}}} placeholder="PACB, IVVD, GOSS"/><span id="ticker-help" className="development-help">Comma- or space-separated symbols</span></label>
          <fieldset className="development-fieldset"><legend>Chart timeframe</legend><div className="grid grid-cols-3 gap-2">{([30,60,300] as const).map(value=><Button key={value} type="button" aria-pressed={timeframe===value} variant={timeframe===value?"default":"ghost"} onClick={()=>setTimeframe(value)}>{value===30?"30 sec":value===60?"1 min":"5 min"}</Button>)}</div></fieldset>
          <label className="development-check"><input type="checkbox" checked={useLatest} onChange={event=>setUseLatest(event.target.checked)}/><span>Use each ticker&apos;s latest Scout detection</span></label>
          {!useLatest&&<label className="development-control"><span className="development-control-label">Detection time</span><Input type="datetime-local" value={when} onChange={event=>setWhen(event.target.value)}/></label>}
          <label className="development-check"><input type="checkbox" checked={inspectRange} onChange={event=>setInspectRange(event.target.checked)}/><span>Inspect a specific chart section</span></label>
          {inspectRange&&<div className="development-range"><div className="grid gap-2 sm:grid-cols-2"><label className="development-control"><span className="development-control-label">Section start</span><Input type="datetime-local" value={rangeStart} onChange={event=>setRangeStart(event.target.value)}/></label><label className="development-control"><span className="development-control-label">Section end</span><Input type="datetime-local" value={rangeEnd} onChange={event=>setRangeEnd(event.target.value)}/></label></div><div className="development-help">Up to 24 hours. Scout selects the latest detection inside this range.</div>
            <label className="development-check development-check-detail"><input type="checkbox" checked={useLiveDetector} onChange={event=>setUseLiveDetector(event.target.checked)}/><span>Run Scout&apos;s live detector over this window <span className="scout-muted">Slower tick-level replay through the production detector; capped at 4 hours.</span></span></label>
            {useLiveDetector&&<label className="block text-xs">Replay engine<select className="mt-1 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-2" value={detectorEngine} onChange={event=>setDetectorEngine(event.target.value as "python"|"rust"|"both")}><option value="rust">Rust recipes (recommended)</option><option value="both">Rust + Python comparison</option><option value="python">Python detector only</option></select></label>}
          </div>}
          <Button className="w-full" aria-busy={busy} disabled={busy} onClick={()=>void run()}>{busy?(inspectRange&&useLiveDetector?"Running live detector…":"Building audits…"):(inspectRange&&useLiveDetector?"Run Scout's live detector":"Run Alpaca chart audit")}</Button>
          {message&&<div className="development-message" role="status" aria-live="polite">{message}</div>}
          <details className="development-captured"><summary>What is captured</summary><ul className="mt-2 space-y-1 scout-muted"><li>Stored Scout detections in the selected range</li><li>Optional production-detector replay over real tick data</li><li>Significance, notification preview, momentum zones, and excursion metrics</li></ul></details>
        </aside>
        <div className="development-results">
          {!items.length&&<div className="development-empty"><IconChartCandle size={20}/><span>Run an audit to create the first chart review.</span></div>}
          {items.map(item=><article key={item.id} className="development-card">
            <div className="development-card-header"><div className="flex items-center gap-2"><b className="ticker-symbol">{item.ticker}</b><Badge data-tone={tone(item.metrics.verdict)}>{item.status==="error"?"ERROR":item.metrics.verdict||"COMPLETE"}</Badge>{item.metrics.use_live_detector&&<Badge data-tone="blue">LIVE DETECTOR REPLAY</Badge>}<span className="text-xs scout-muted">{item.timeframe_seconds}s · {new Date(item.detection_at*1000).toLocaleString()}</span></div>{item.finding_id&&<a className="text-xs text-[var(--blue)]" href={`/?ticker=${item.ticker}&finding=${item.finding_id}`}>Open Scout detection →</a>}</div>
            {item.status==="error"?<div className="p-5 text-sm text-[var(--red)]">{item.error}</div>:<div className="development-card-body">
              <div>{item.metrics.detection_note&&<div className="border-b border-[var(--border)] px-3 py-2 text-xs scout-muted">{item.metrics.detection_note}</div>}{item.chart_url&&<button type="button" className="group relative block w-full cursor-zoom-in" onClick={()=>setExpandedChart({evaluationId:item.id,ticker:item.ticker,src:`${API_BASE}${item.chart_url}`,alt:`${item.ticker} formation audit with marked detections`})} aria-label={`Enlarge and annotate ${item.ticker} formation audit chart`}><img src={`${API_BASE}${item.chart_url}`} alt={`${item.ticker} formation audit with marked detections`} className="w-full bg-white/5"/><span className="absolute right-3 top-3 flex items-center gap-1 rounded-md bg-black/75 px-2 py-1 text-xs text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"><IconZoomIn size={15}/> Enlarge & annotate</span></button>}<div className="flex flex-wrap gap-3 border-t border-[var(--border)] px-3 py-2 text-[10px] scout-muted"><span><b className="text-[#ff5d73]">Red</b> Tier 1 breakout</span><span><b className="text-[#4aa8ff]">Blue</b> Tier 2 continuation</span><span><b className="text-[#6b7686]">Gray</b> Tier 3 bounce</span><span><b className="text-[#39d2c0]">Cyan star</b> would notify (preview)</span><span><b className="text-[#2ed6a1]">Green shading</b> real momentum, caught</span><span><b className="text-[#ffb020]">Orange shading</b> real momentum, missed</span><span>Shading = +15s to +30s area</span></div></div>
              <EvaluationVisuals metrics={item.metrics}/><div className="development-metrics grid grid-cols-2 gap-px text-xs xl:grid-cols-1">{[
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

type EvaluationMetrics=DevelopmentEvaluation["metrics"];
const chartAxis={fontSize:9,fill:"var(--muted-2)"};
const chartGrid={stroke:"rgba(139,159,181,.08)"};
const scored=(value:number|undefined|null)=>value==null?null:Math.max(0,Math.min(100,value));

function EvaluationVisuals({metrics}:{metrics:EvaluationMetrics}){
  const excursion=[
    {window:"30s",value:metrics.max_30s_pct??null},{window:"1m",value:metrics.max_1m_pct??null},
    {window:"5m",value:metrics.max_5m_pct??null},{window:"15m",value:metrics.max_15m_pct??null},
  ];
  const tiers=[
    {name:"Tier 1",value:metrics.tier_counts?.tier_1??0},{name:"Tier 2",value:metrics.tier_counts?.tier_2??0},
    {name:"Tier 3",value:metrics.tier_counts?.tier_3??0},{name:"Notify",value:metrics.would_notify_preview_marked??0},
    {name:"Gate",value:metrics.gate_passes_marked??0},
  ];
  const precision=scored(metrics.objective_zone_metrics?.precision_pct);
  const recall=scored(metrics.objective_zone_metrics?.recall_pct);
  const capture=scored(metrics.capture_efficiency_pct);
  const gauge=[{name:"Capture",value:capture??0,fill:"var(--green)"}];
  const evidence=[
    ["Supply family",metrics.unified_evidence?.supply],
    ["Compression",metrics.unified_evidence?.compression_quality],
    ["Box quality",metrics.unified_evidence?.box?.quality],
    ["Pullback quality",metrics.unified_evidence?.pullback?.quality],
  ] as const;
  return <div className="development-visuals">
    <div className="development-stat-grid">
      <StatTile label="MFE" value={metrics.max_favorable_r==null?"—":`${num(metrics.max_favorable_r)}R`} tone="green"/>
      <StatTile label="MAE" value={metrics.max_adverse_r==null?"—":`${num(metrics.max_adverse_r)}R`} tone="red"/>
      <StatTile label="Capture efficiency" value={capture==null?"—":`${num(capture,1)}%`} tone="blue"/>
      <div className="development-stat-tile"><span>Outcome</span><Badge data-tone={tone(metrics.verdict)}>{metrics.verdict||"UNSCORED"}</Badge><small>{metrics.first_touch||"No first-touch result"}</small></div>
    </div>
    <div className="development-chart-grid">
      <div className="development-chart-panel"><div className="development-chart-heading"><span>FORWARD EXCURSION</span><small>maximum favorable move</small></div><ChartContainer className="h-[132px]" config={{move:{color:"var(--green)"}}}><BarChart data={excursion} margin={{top:8,right:8,bottom:0,left:-18}}><CartesianGrid vertical={false} {...chartGrid}/><XAxis dataKey="window" tick={chartAxis} axisLine={false} tickLine={false}/><YAxis tick={chartAxis} axisLine={false} tickLine={false}/><Bar dataKey="value" name="Move %" fill="var(--green)" radius={[3,3,0,0]}><Cell fill="var(--green)"/><Cell fill="var(--blue)"/><Cell fill="var(--cyan)"/><Cell fill="var(--orange)"/></Bar><RechartsTooltip content={<ChartTooltipContent/>}/></BarChart></ChartContainer></div>
      <div className="development-chart-panel"><div className="development-chart-heading"><span>DETECTION COVERAGE</span><small>marked events by class</small></div><ChartContainer className="h-[132px]" config={{coverage:{color:"var(--blue)"}}}><RadarChart data={tiers} outerRadius="66%"><PolarGrid stroke="rgba(139,159,181,.12)"/><PolarAngleAxis dataKey="name" tick={chartAxis}/><Radar dataKey="value" name="Count" stroke="var(--blue)" fill="var(--blue)" fillOpacity={.24}/><RechartsTooltip content={<ChartTooltipContent/>}/></RadarChart></ChartContainer></div>
      <div className="development-chart-panel development-gauge-panel"><div className="development-chart-heading"><span>CAPTURE QUALITY</span><small>available move retained</small></div>{capture==null?<div className="development-unscored">No capture score</div>:<div className="development-gauge"><ChartContainer className="h-[132px]" config={{capture:{color:"var(--green)"}}}><RadialBarChart data={gauge} startAngle={220} endAngle={-40} innerRadius="66%" outerRadius="94%"><RadialBar dataKey="value" background={{fill:"rgba(139,159,181,.10)"}} cornerRadius={5}/></RadialBarChart></ChartContainer><strong>{num(capture,1)}%</strong></div>}</div>
    </div>
    <div className="development-evidence-panel"><div className="development-chart-heading"><span>STRUCTURE EVIDENCE</span><small>{metrics.unified_evidence?.phase||"advisory context"}</small></div><div className="development-evidence-grid">{evidence.map(([label,value])=><div key={label} className="development-evidence-row"><div><span>{label}</span><b>{value==null?"—":num(value,0)}</b></div><div className="development-progress"><i style={{width:`${scored(value)??0}%`}}/></div></div>)}<div className="development-evidence-row"><div><span>Detector recall</span><b>{recall==null?"—":`${num(recall,0)}%`}</b></div><div className="development-progress"><i data-tone="blue" style={{width:`${recall??0}%`}}/></div></div><div className="development-evidence-row"><div><span>Detector precision</span><b>{precision==null?"—":`${num(precision,0)}%`}</b></div><div className="development-progress"><i data-tone="cyan" style={{width:`${precision??0}%`}}/></div></div></div></div>
  </div>;
}

function StatTile({label,value,tone:tileTone}:{label:string;value:string;tone:"green"|"red"|"blue"}){return <div className="development-stat-tile"><span>{label}</span><strong data-tone={tileTone}>{value}</strong></div>}

function JsonCard({title,value}:{title:string;value:unknown}){return <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4"><h3 className="font-semibold">{title}</h3><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-3 text-[11px] text-[var(--muted-foreground)]">{JSON.stringify(value??{},null,2)}</pre></div>}
function ToolCard({title,text,action}:{title:string;text:string;action:()=>void}){return <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-5"><h3 className="font-semibold">{title}</h3><p className="my-3 text-xs scout-muted">{text}</p><Button onClick={action}>Open</Button></div>}
function InsightTable({items}:{items:DevelopmentEvaluation[]}){const grouped=Object.values(items.reduce<Record<string,{key:string,count:number,wins:number,r:number}>>((all,item)=>{const f=item.metrics.formation||{};const key=`${f.stage||"UNKNOWN"} · ${f.rank||"—"} · ${item.timeframe_seconds}s`;const row=all[key]||{key,count:0,wins:0,r:0};row.count++;row.wins+=item.metrics.verdict==="WINNER"?1:0;row.r+=item.metrics.max_favorable_r||0;all[key]=row;return all;},{}));return <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)]"><div className="border-b border-[var(--border)] p-4 font-semibold">Performance by Scout formation cohort</div><table className="w-full text-left text-xs"><thead className="scout-muted"><tr><th className="p-3">Cohort</th><th>Samples</th><th>Winner rate</th><th>Average MFE</th></tr></thead><tbody>{grouped.map(row=><tr key={row.key} className="border-t border-[var(--border)]"><td className="p-3">{row.key}</td><td>{row.count}</td><td>{num(row.wins/row.count*100,1)}%</td><td>{num(row.r/row.count)}R</td></tr>)}</tbody></table></div>}
