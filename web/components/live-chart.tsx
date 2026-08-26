"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, API_CONFIGURED, getMarketSnapshot, peekMarketSnapshot } from "@/lib/api";
import { Button } from "@/components/ui/button";
import type { Bucket, Finding, MarketSnapshot } from "@/lib/types";

function emaSeries(values: number[], length: number) {
  if (!values.length) return [];
  const alpha = 2 / (length + 1);
  const out = [values[0]];
  for (let i = 1; i < values.length; i += 1) out.push(alpha * values[i] + (1 - alpha) * out[i - 1]);
  return out;
}

function vwapSeries(rows: Bucket[]) {
  let pv = 0;
  let vv = 0;
  return rows.map((b) => {
    const typical = (b.high + b.low + b.close) / 3;
    pv += typical * b.volume;
    vv += b.volume;
    return vv ? pv / vv : b.close;
  });
}

function moneyPrice(value:number){return value<1?`$${value.toFixed(4)}`:`$${value.toFixed(2)}`;}

function aggregateBuckets(rows: Bucket[], seconds: number): Bucket[] {
  if (seconds <= 15) return rows;
  const groups = new Map<number, Bucket>();
  for (const row of rows) {
    const start = Math.floor(row.start_ts / seconds) * seconds;
    const current = groups.get(start);
    if (!current) groups.set(start, { ...row, start_ts: start });
    else {
      current.high = Math.max(current.high, row.high);
      current.low = Math.min(current.low, row.low);
      current.close = row.close;
      current.volume += row.volume;
      current.trades += row.trades;
    }
  }
  return Array.from(groups.values()).sort((a,b)=>a.start_ts-b.start_ts);
}

function fillBucketGaps(rows: Bucket[], seconds: number): Bucket[] {
  if (rows.length < 2) return rows;
  const filled: Bucket[] = [rows[0]];
  const maxFillIntervals = Math.max(1, Math.floor((15 * 60) / seconds));
  for (const row of rows.slice(1)) {
    const previous = filled.at(-1)!;
    const missingIntervals = Math.round((row.start_ts - previous.start_ts) / seconds) - 1;
    if (missingIntervals > 0 && missingIntervals <= maxFillIntervals) {
      for (let step = 1; step <= missingIntervals; step += 1) {
        filled.push({
          ...previous,
          start_ts: previous.start_ts + step * seconds,
          open: previous.close,
          high: previous.close,
          low: previous.close,
          close: previous.close,
          volume: 0,
          trades: 0,
        });
      }
    }
    filled.push(row);
  }
  return filled;
}

function demoSnapshot(finding?: Finding): MarketSnapshot {
  const now = 1_800_000_000; // deterministic demo timestamp; prevents SSR hydration drift
  const base = finding?.price || 2.4;
  const rows: Bucket[] = [];
  let px = base * 0.92;
  for (let i = 0; i < 70; i += 1) {
    const drift = i < 42 ? Math.sin(i / 4) * 0.001 : (i - 42) * 0.0018;
    const open = px;
    const close = base * (0.92 + i * 0.00035 + drift);
    const high = Math.max(open, close) * (1 + 0.0015 + (i % 4) * 0.0004);
    const low = Math.min(open, close) * (1 - 0.0012);
    rows.push({ start_ts: now - (69 - i) * 15, open, high, low, close, volume: 100 + Math.max(0, i - 42) * 70 + (i % 5) * 20, trades: 2 + (i % 7) });
    px = close;
  }
  return {
    ticker: finding?.ticker || "SCOUT",
    session_date: "demo",
    session_first_price: rows[0].open,
    buckets: rows,
    metrics: {
      price: rows.at(-1)?.close,
      ema9: finding?.ema9,
      ema21: finding?.ema21,
      vwap: finding?.vwap,
      change15: finding?.change_15s_pct ?? 1.2,
      change30: finding?.change_30s_pct ?? 2.1,
      change60: finding?.change_60s_pct ?? 3.4,
      vol15: finding?.vol_ratio_15s ?? 8.6,
      breakout: finding?.signals?.includes("BREAKOUT") ?? true,
      surge: finding?.signals?.includes("SURGE") ?? true,
    },
    halt: null,
    findings: finding ? [finding] : [],
    catalysts: [],
    statuses: [],
  };
}

function markerTone(stage: string) {
  if (stage === "PRE_IGNITION" || stage === "ARMED") return "var(--orange)";
  if (stage === "SURGE" || stage === "IGNITION") return "var(--green)";
  if (stage === "BREAKOUT" || stage === "EARLY") return "var(--blue)";
  if (stage === "HALT") return "var(--red)";
  if (["CATALYST","CATALYST_WATCH","CATALYST_ACTIVE"].includes(stage)) return "var(--cyan)";
  return "var(--orange)";
}

export type ChartAnnotations = { formation?:boolean; detection?:boolean; trigger?:boolean; invalidation?:boolean };
export function LiveChart({ finding, frozen = false, active = true, pollOffsetMs = 0, refreshNonce = 0, timeframeSeconds = 15, showAnnotations = true, annotations = {}, onSnapshot, onSelectFinding }: { finding?: Finding; frozen?: boolean; active?: boolean; pollOffsetMs?: number; refreshNonce?: number; timeframeSeconds?: 15|30|60|300; showAnnotations?:boolean; annotations?:ChartAnnotations; onSnapshot?: (snapshot: MarketSnapshot | null) => void; onSelectFinding?: (finding: Finding) => void }) {
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(() => finding ? (API_CONFIGURED ? peekMarketSnapshot(finding.ticker,finding.detected_at,timeframeSeconds,finding.id) : demoSnapshot(finding)) : null);
  const [loading,setLoading]=useState(Boolean(finding&&API_CONFIGURED&&!snapshot));
  const [error, setError] = useState<string>("");

  useEffect(() => {
    if (!finding) {
      setSnapshot(null);
      onSnapshot?.(null);
      return;
    }
    if (!API_CONFIGURED) {
      const demo = demoSnapshot(finding);
      setSnapshot(demo);
      onSnapshot?.(demo);
      return;
    }
    let disposed = false;
    const cached=peekMarketSnapshot(finding.ticker,finding.detected_at,timeframeSeconds,finding.id);
    if(cached){setSnapshot(cached);setLoading(false);onSnapshot?.(cached);}
    else { setSnapshot(null); setLoading(true); }
    async function load(force=false) {
      try {
        const next = await getMarketSnapshot(finding!.ticker, finding!.detected_at, timeframeSeconds, finding!.id, force);
        if (!disposed) {
          setSnapshot(next);
          setLoading(false);
          setError("");
          onSnapshot?.(next);
        }
      } catch (err) {
        if (!disposed) { setLoading(false); setError(err instanceof Error ? err.message : "Live chart unavailable"); }
      }
    }
    // Symbol changes must feel immediate. Stagger only background refreshes, never the first paint.
    void load(false);
    const followsLiveEdge = Date.now() / 1000 - finding.detected_at < 30 * 60;
    const intervalMs=active?3000:9000;
    let timer:number|null=null;
    const arm=()=>{timer=followsLiveEdge?window.setInterval(()=>{if(!document.hidden)void load(true);},intervalMs):null;};
    const stagger=window.setTimeout(arm,Math.max(0,pollOffsetMs));
    return () => { disposed = true; window.clearTimeout(stagger); if(timer!=null)window.clearInterval(timer); };
  }, [finding?.ticker, finding?.detected_at, timeframeSeconds, onSnapshot, active, pollOffsetMs, refreshNonce]);

  if (!finding) return <div className="chart-empty">Select a ticker</div>;
  if (frozen && finding.chart_url && API_CONFIGURED) {
    return <div className="chart-stage"><img src={`${API_BASE}${finding.chart_url}`} alt={`${finding.ticker} frozen detection chart`} className="chart-image"/></div>;
  }
  if (!snapshot?.buckets?.length) return <div className="chart-empty"><span>{error || (loading ? `Loading ${finding.ticker}…` : "Waiting for live buckets…")}</span></div>;

  return <SvgChart snapshot={snapshot} selectedFinding={finding} error={error} timeframeSeconds={timeframeSeconds} showAnnotations={showAnnotations} annotations={annotations} onSelectFinding={onSelectFinding}/>;
}

function SvgChart({ snapshot, selectedFinding, error, timeframeSeconds, showAnnotations, annotations, onSelectFinding }: { snapshot: MarketSnapshot; selectedFinding: Finding; error?: string; timeframeSeconds:15|30|60|300; showAnnotations:boolean; annotations:ChartAnnotations; onSelectFinding?: (finding: Finding) => void }) {
  const allRows = useMemo(() => fillBucketGaps(aggregateBuckets(snapshot.buckets, timeframeSeconds), timeframeSeconds), [snapshot.buckets,timeframeSeconds]);
  const [visibleCount,setVisibleCount]=useState(64);
  const [rightOffset,setRightOffset]=useState(0);
  const [hoverIndex,setHoverIndex]=useState<number|null>(null);
  const drag=useRef<{x:number;offset:number}|null>(null);
  const pinch=useRef<{distance:number;count:number}|null>(null);
  const safeCount=Math.max(12,Math.min(160,visibleCount,allRows.length));
  const safeOffset=Math.max(0,Math.min(rightOffset,Math.max(0,allRows.length-safeCount)));
  const end=Math.max(safeCount,allRows.length-safeOffset);
  const rows=allRows.slice(Math.max(0,end-safeCount),end);
  const layout = useMemo(() => {
    const width = 1000;
    const height = 520;
    const left = 14;
    const right = 70;
    const top = 18;
    const priceBottom = 404;
    const volumeTop = 420;
    const bottom = 486;
    const highs = rows.map((b) => b.high);
    const lows = rows.map((b) => b.low);
    let min = Math.min(...lows);
    let max = Math.max(...highs);
    const pad = Math.max((max - min) * 0.08, max * 0.002);
    min -= pad;
    max += pad;
    const firstTs = rows[0]?.start_ts ?? 0;
    const lastTs = rows.at(-1)?.start_ts ?? firstTs + 15;
    const domainEnd = lastTs + timeframeSeconds;
    const timeSpan = Math.max(15, domainEnd - firstTs);
    const plotWidth = width - left - right;
    const x = (ts: number) => left + ((ts - firstTs) / timeSpan) * plotWidth;
    const candleSlot = plotWidth * timeframeSeconds / timeSpan;
    const candleX = (startTs: number) => x(startTs + timeframeSeconds / 2);
    const bodyW = Math.max(3.5, Math.min(18, candleSlot * 0.72));
    const y = (price: number) => top + ((max - price) / Math.max(max - min, 1e-9)) * (priceBottom - top);
    const maxVol = Math.max(1, ...rows.map((b) => b.volume));
    const vy = (vol: number) => bottom - (vol / maxVol) * (bottom - volumeTop);
    return { width, height, left, right, top, priceBottom, volumeTop, bottom, min, max, firstTs, lastTs, domainEnd, timeSpan, candleSlot, bodyW, x, candleX, y, vy };
  }, [rows,timeframeSeconds]);

  const ema9 = useMemo(() => emaSeries(rows.map((b) => b.close), 9), [rows]);
  const ema21 = useMemo(() => emaSeries(rows.map((b) => b.close), 21), [rows]);
  const vwap = useMemo(() => vwapSeries(rows), [rows]);
  const path = (values: number[]) => values.map((v, i) => `${i ? "L" : "M"}${layout.candleX(rows[i].start_ts).toFixed(1)},${layout.y(v).toFixed(1)}`).join(" ");
  const firstTs = rows[0]?.start_ts ?? 0;
  const lastTs = rows.at(-1)?.start_ts ?? firstTs;
  const markerX = (ts: number) => layout.x(ts);
  const inWindow=(ts:number)=>ts>=firstTs&&ts<layout.domainEnd;
  const findingMarkers = snapshot.findings.filter((f) => inWindow(f.detected_at)).slice().reverse();
  const findingClusters = useMemo(() => {
    const groups = new Map<string, Finding[]>();
    for (const finding of findingMarkers) {
      const bucket = Math.floor(finding.detected_at / timeframeSeconds) * timeframeSeconds;
      const key = String(bucket);
      const current = groups.get(key) || [];
      current.push(finding);
      groups.set(key, current);
    }
    return Array.from(groups.entries()).map(([bucket, items]) => {
      const selected = items.find(item => item.id === selectedFinding.id);
      const lead = selected || items.slice().sort((a,b)=>(b.score||0)-(a.score||0))[0];
      return { bucket:Number(bucket), items, lead, selected:Boolean(selected) };
    });
  }, [findingMarkers, timeframeSeconds, selectedFinding.id]);
  const catalystMarkers = snapshot.catalysts.filter((c) => c.published_at >= firstTs - 15 && c.published_at <= lastTs + 30).slice(-6);
  const statusMarkers = snapshot.statuses.filter((s) => s.event_at >= firstTs - 15 && s.event_at <= lastTs + 30).slice(-4);
  const current = rows.at(-1)?.close ?? selectedFinding.price;
  const priceTicks = Array.from({ length: 5 }, (_, i) => layout.max - ((layout.max - layout.min) * i) / 4);
  const timeTicks = Array.from({ length: 5 }, (_, i) => firstTs + ((layout.domainEnd - firstTs) * i) / 4);
  const etTime = (ts: number) => new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit" }).format(ts * 1000);
  const exactEtTime=(ts:number)=>`${new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit",second:"2-digit",fractionalSecondDigits:3}).format(ts*1000)} ET`;
  const detectionCandle=rows.find(row=>selectedFinding.detected_at>=row.start_ts&&selectedFinding.detected_at<row.start_ts+timeframeSeconds);
  const detectionOffset=detectionCandle?selectedFinding.detected_at-detectionCandle.start_ts:null;
  const deliveryMarkers=(snapshot.delivery||[]).filter(item=>inWindow(item.event_at));
  const hovered=hoverIndex==null?null:rows[hoverIndex];
  const pointerIndex=(event:React.PointerEvent<SVGSVGElement>)=>{
    const box=event.currentTarget.getBoundingClientRect();
    const x=((event.clientX-box.left)/box.width)*layout.width;
    const ratio=Math.max(0,Math.min(1,(x-layout.left)/(layout.width-layout.left-layout.right)));
    return Math.max(0,Math.min(rows.length-1,Math.round(ratio*(rows.length-1))));
  };
  const resetViewport=()=>{setVisibleCount(Math.min(64,Math.max(12,allRows.length)));setRightOffset(0);setHoverIndex(null);};
  const zoom=(delta:number)=>setVisibleCount(value=>Math.max(12,Math.min(160,value+delta)));
  const touchDistance=(touches:React.TouchList)=>touches.length<2?0:Math.hypot(touches[0].clientX-touches[1].clientX,touches[0].clientY-touches[1].clientY);

  return <div className="chart-stage">
    <svg viewBox={`0 0 ${layout.width} ${layout.height}`} preserveAspectRatio="none" className="chart-svg chart-interactive" role="img" aria-label={`${snapshot.ticker} ${timeframeSeconds}-second candlestick chart. Use the mouse wheel to zoom and drag to pan.`}
      onWheel={event=>{event.preventDefault();setVisibleCount(value=>Math.max(12,Math.min(160,value+(event.deltaY>0?8:-8))));}}
      onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);drag.current={x:event.clientX,offset:safeOffset};}}
      onPointerMove={event=>{setHoverIndex(pointerIndex(event));if(drag.current){const box=event.currentTarget.getBoundingClientRect();const candlePx=box.width/Math.max(1,safeCount);setRightOffset(Math.max(0,Math.round(drag.current.offset+(event.clientX-drag.current.x)/candlePx)));}}}
      onPointerUp={event=>{event.currentTarget.releasePointerCapture(event.pointerId);drag.current=null;}}
      onPointerLeave={()=>{setHoverIndex(null);drag.current=null;}}
      onDoubleClick={resetViewport}
      onTouchStart={event=>{if(event.touches.length===2)pinch.current={distance:touchDistance(event.touches),count:safeCount};}}
      onTouchMove={event=>{if(event.touches.length===2&&pinch.current){event.preventDefault();const next=touchDistance(event.touches);if(next>0){const scale=pinch.current.distance/next;setVisibleCount(Math.max(12,Math.min(160,Math.round(pinch.current.count*scale))));}}}}
      onTouchEnd={()=>{pinch.current=null;}}>
      <g className="chart-grid-lines">
        {priceTicks.map((p) => <line key={p} x1={layout.left} x2={layout.width-layout.right} y1={layout.y(p)} y2={layout.y(p)}/>) }
        {timeTicks.slice(1,-1).map((ts) => <line key={ts} x1={layout.x(ts)} x2={layout.x(ts)} y1={layout.top} y2={layout.bottom}/>) }
      </g>
      <line className="chart-volume-separator" x1={layout.left} x2={layout.width-layout.right} y1={layout.volumeTop-8} y2={layout.volumeTop-8}/>
      <g className="chart-volume-bars">
        {rows.map((b) => {
          const up = b.close >= b.open;
          return <rect key={`volume-${b.start_ts}`} className={`chart-volume-bar chart-volume-bar-${up?"up":"down"}`} x={layout.candleX(b.start_ts)-layout.bodyW/2} y={layout.vy(b.volume)} width={layout.bodyW} height={layout.bottom-layout.vy(b.volume)}/>;
        })}
      </g>
      <path d={path(ema9)} fill="none" stroke="var(--blue)" strokeWidth="1.25" vectorEffect="non-scaling-stroke" opacity=".66"/>
      <path d={path(ema21)} fill="none" stroke="var(--orange)" strokeWidth="1.1" vectorEffect="non-scaling-stroke" opacity=".62"/>
      <path d={path(vwap)} fill="none" stroke="var(--cyan)" strokeWidth="1" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" opacity=".6"/>
      {showAnnotations&&annotations.detection!==false&&detectionCandle&&<rect className="detection-candle-highlight" x={layout.candleX(detectionCandle.start_ts)-layout.candleSlot/2} y={layout.top} width={layout.candleSlot} height={layout.priceBottom-layout.top}/>}
      <g className="chart-candles">
        {rows.map((b) => {
          const cx = layout.candleX(b.start_ts);
          const bodyW = layout.bodyW;
          const yo = layout.y(b.open);
          const yc = layout.y(b.close);
          const up = b.close >= b.open;
          const bodyH = Math.max(2.2, Math.abs(yc - yo));
          const bodyY = (yo + yc) / 2 - bodyH / 2;
          return <g key={b.start_ts} className={`chart-candle chart-candle-${up?"up":"down"}${b.trades?"":" chart-candle-empty"}`}>
            <line className="chart-candle-wick" x1={cx} x2={cx} y1={layout.y(b.high)} y2={layout.y(b.low)}/>
            <rect className="chart-candle-body" x={cx-bodyW/2} y={bodyY} width={bodyW} height={bodyH} rx="0.35"/>
          </g>;
        })}
      </g>

      {showAnnotations && annotations.formation !== false && selectedFinding.formation_start_at && selectedFinding.formation_end_at && <g className="chart-formation-region">
        <rect x={markerX(selectedFinding.formation_start_at)} y={layout.top} width={Math.max(4,markerX(selectedFinding.formation_end_at)-markerX(selectedFinding.formation_start_at))} height={layout.priceBottom-layout.top} fill="var(--blue)" opacity=".08"/>
        <line x1={markerX(selectedFinding.formation_start_at)} x2={markerX(selectedFinding.formation_start_at)} y1={layout.top} y2={layout.priceBottom} stroke="var(--blue)" strokeDasharray="3 4" opacity=".5"/>
        <line x1={markerX(selectedFinding.formation_end_at)} x2={markerX(selectedFinding.formation_end_at)} y1={layout.top} y2={layout.priceBottom} stroke="var(--blue)" strokeDasharray="3 4" opacity=".5"/>
      </g>}
      {showAnnotations && annotations.trigger !== false && selectedFinding.trigger_level && <line x1={layout.left} x2={layout.width-layout.right} y1={layout.y(selectedFinding.trigger_level)} y2={layout.y(selectedFinding.trigger_level)} stroke="var(--green)" strokeDasharray="5 4" opacity=".5"/>}
      {showAnnotations && annotations.invalidation !== false && selectedFinding.invalidation_level && <line x1={layout.left} x2={layout.width-layout.right} y1={layout.y(selectedFinding.invalidation_level)} y2={layout.y(selectedFinding.invalidation_level)} stroke="var(--red)" strokeDasharray="5 4" opacity=".42"/>}

      {catalystMarkers.map((c, idx) => {
        const x = markerX(c.published_at);
        return <g key={`c-${c.id}-${idx}`} className="chart-event-marker"><line x1={x} x2={x} y1={layout.top} y2={layout.priceBottom} stroke="var(--cyan)" strokeDasharray="2 4" opacity=".45"/><text x={x+4} y={28 + (idx%2)*12} className="chart-event-label" fill="var(--cyan)">CAT</text></g>;
      })}
      {showAnnotations && annotations.detection !== false && findingClusters.map((cluster) => {
        const f=cluster.lead;
        const x=markerX(f.detected_at);
        const fused=Array.from(new Set([f.stage,...(f.signals||[])].filter(Boolean)));
        const primary=fused.find(signal=>["PRE_IGNITION","ARMED","REARM","VWAP_RECLAIM","EMA_RECLAIM","RECLAIM","IGNITION","BREAKOUT","SURGE","STAIRCASE","EARLY","REVERSAL_WATCH"].includes(signal))||f.stage;
        const color=markerTone(primary);
        const selected=cluster.selected;
        const y=layout.y(f.price);
        const label=selected?`${f.stage.replaceAll("_"," ")} · ${exactEtTime(f.detected_at)}`:`${cluster.items.length} Scout event${cluster.items.length===1?"":"s"}`;
        return <g key={`fc-${cluster.bucket}`} className="chart-event-chip" role="button" tabIndex={0} aria-label={label}
          onClick={event=>{event.stopPropagation();onSelectFinding?.(f);}}
          onKeyDown={event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();onSelectFinding?.(f);}}}>
          {selected&&<line x1={x} x2={x} y1={layout.top} y2={layout.priceBottom} stroke={color} strokeDasharray="1 0" opacity=".72"/>}
          <circle cx={x} cy={y} r={selected?6:cluster.items.length>1?6:4} fill={color} opacity={selected?1:.9}/>
          {cluster.items.length>1&&<text x={x} y={y+3} textAnchor="middle" className="chart-cluster-count">{cluster.items.length>9?"9+":cluster.items.length}</text>}
          {selected&&<text x={x+8} y={Math.max(layout.top+14,y-8)} className="chart-event-label" fill={color}>DETECTED · {primary.replaceAll("_"," ")}</text>}
        </g>;
      })}
      {deliveryMarkers.map((event,idx)=>{const x=markerX(event.event_at);const sent=event.status==="sent"||event.status==="delivered";return <g key={`delivery-${event.id}`} className="chart-delivery-marker"><line x1={x} x2={x} y1={layout.top} y2={layout.priceBottom} stroke={sent?"var(--green)":"var(--orange)"} strokeDasharray="1 4" opacity=".75"/><circle cx={x} cy={layout.top+8+(idx%2)*8} r="2.5" fill={sent?"var(--green)":"var(--orange)"}/></g>})}
      {statusMarkers.map((s, idx) => {
        const x = markerX(s.event_at);
        return <g key={`s-${s.id ?? idx}`} className="chart-event-marker"><line x1={x} x2={x} y1={layout.top} y2={layout.priceBottom} stroke={s.is_halted ? "var(--red)" : "var(--green)"} strokeDasharray="2 3"/><text x={x+4} y={92 + (idx%2)*12} className="chart-event-label" fill={s.is_halted ? "var(--red)" : "var(--green)"}>{s.is_halted ? "HALT" : "RESUME"}</text></g>;
      })}

      <line x1={layout.left} x2={layout.width-layout.right} y1={layout.y(current)} y2={layout.y(current)} stroke="var(--text)" strokeDasharray="2 4" opacity=".18"/>
      <rect x={layout.width-layout.right+4} y={layout.y(current)-9} width="62" height="18" rx="4" fill="var(--panel-3)"/>
      <text x={layout.width-layout.right+35} y={layout.y(current)+3} textAnchor="middle" className="chart-price-label">{current < 1 ? current.toFixed(4) : current.toFixed(2)}</text>
      {priceTicks.map((p) => <text key={`t-${p}`} x={layout.width-layout.right+8} y={layout.y(p)+3} className="chart-axis-label">{p < 1 ? p.toFixed(4) : p.toFixed(2)}</text>)}
      {timeTicks.map((ts, i) => <text key={`time-${i}`} x={layout.x(ts)} y="510" textAnchor={i===0?"start":i===timeTicks.length-1?"end":"middle"} className="chart-time-label">{etTime(ts)}</text>)}
      <text x={layout.width-layout.right+7} y="510" className="chart-timezone-label">ET</text>
      {hovered&&<g className="chart-crosshair">
        <line x1={layout.candleX(hovered.start_ts)} x2={layout.candleX(hovered.start_ts)} y1={layout.top} y2={layout.bottom}/>
        <line x1={layout.left} x2={layout.width-layout.right} y1={layout.y(hovered.close)} y2={layout.y(hovered.close)}/>
      </g>}
    </svg>
    <div className="chart-overlay-top">
      <span>EMA9</span><i className="legend-ema9"/><span>EMA21</span><i className="legend-ema21"/><span>VWAP</span><i className="legend-vwap"/>
      <span className="chart-source">{snapshot.source?.replaceAll("-"," ")||"live"}</span>
      {snapshot.source?.startsWith("historical")&&<span className="chart-source">{snapshot.historical_complete===false?"INCOMPLETE":`${snapshot.historical_trade_count??0} trades · ${snapshot.historical_pages??1}p`}</span>}
      <span className="ml-auto flex gap-1 pointer-events-auto"><Button variant="ghost" className="h-6 px-1.5 text-[10px]" aria-label="Zoom in" onClick={()=>zoom(-8)}>＋</Button><Button variant="ghost" className="h-6 px-1.5 text-[10px]" aria-label="Zoom out" onClick={()=>zoom(8)}>−</Button><Button variant="ghost" className="h-6 px-1.5 text-[10px]" onClick={resetViewport}>Fit</Button></span>
    </div>
    {hovered&&<div className="chart-crosshair-readout"><b>{etTime(hovered.start_ts)} ET</b><span>O {moneyPrice(hovered.open)}</span><span>H {moneyPrice(hovered.high)}</span><span>L {moneyPrice(hovered.low)}</span><span>C {moneyPrice(hovered.close)}</span><span>V {hovered.volume.toLocaleString()}</span></div>}
    {detectionCandle&&<div className="detection-audit"><b>Detected {exactEtTime(selectedFinding.detected_at)}</b><span>{detectionOffset?.toFixed(3)}s into candle</span><span>{moneyPrice(selectedFinding.price)} detection</span><span>{moneyPrice(detectionCandle.low)}–{moneyPrice(detectionCandle.high)} candle</span></div>}
    {error && <div className="chart-connection-note">{error}</div>}
  </div>;
}
