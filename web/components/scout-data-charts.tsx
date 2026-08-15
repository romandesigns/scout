"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, ComposedChart, Line, PolarAngleAxis, PolarGrid, Radar, RadarChart, RadialBar, RadialBarChart, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import type { Finding, ValidationRow } from "@/lib/types";

const axis={fontSize:9,fill:"var(--muted-2)"};

export function CandidateProfileChart({finding}:{finding:Finding}){
  const profile=finding.candidate_profile||{};
  const data=[
    {name:"Velocity",value:profile.velocity??0},{name:"Participation",value:profile.participation??0},
    {name:"Structure",value:profile.structure??0},{name:"Catalyst",value:profile.catalyst??0},
    {name:"Quality",value:profile.quality??finding.quality_score??0},
  ];
  return <ChartContainer className="h-[170px]" config={{profile:{label:"Candidate profile",color:"var(--blue)"}}}><RadarChart data={data} outerRadius="68%"><PolarGrid stroke="rgba(139,159,181,.14)"/><PolarAngleAxis dataKey="name" tick={axis}/><Radar dataKey="value" stroke="var(--blue)" fill="var(--blue)" fillOpacity={.22}/><ChartTooltip content={<ChartTooltipContent/>}/></RadarChart></ChartContainer>;
}

export function QualityGauge({finding}:{finding:Finding}){
  const value=finding.quality_score??0;
  const color=finding.quality_label==="CLEAN"?"var(--green)":finding.quality_label==="DEVELOPING"?"var(--blue)":finding.quality_label==="CHOPPY"?"var(--orange)":"var(--red)";
  return <div className="quality-gauge"><ChartContainer config={{quality:{color}}}><RadialBarChart data={[{name:"Quality",value,fill:color}]} startAngle={210} endAngle={-30} innerRadius="68%" outerRadius="96%"><RadialBar dataKey="value" background={{fill:"rgba(139,159,181,.09)"}} cornerRadius={8}/></RadialBarChart></ChartContainer><div><b>{value}</b><span>{finding.quality_label||"UNRATED"}</span></div></div>;
}

export function VelocityChart({finding}:{finding:Finding}){
  const data=[{window:"3s",move:finding.change_3s_pct??0},{window:"5s",move:finding.change_5s_pct??0},{window:"10s",move:finding.change_10s_pct??0},{window:"15s",move:finding.change_15s_pct??0},{window:"30s",move:finding.change_30s_pct??0},{window:"60s",move:finding.change_60s_pct??0}];
  return <ChartContainer className="h-[130px]" config={{move:{label:"Price change %",color:"var(--green)"}}}><BarChart data={data} margin={{top:5,right:4,bottom:0,left:-24}}><CartesianGrid vertical={false} stroke="rgba(139,159,181,.08)"/><XAxis dataKey="window" tick={axis} axisLine={false} tickLine={false}/><YAxis tick={axis} axisLine={false} tickLine={false}/><Bar dataKey="move" name="Move %" fill="var(--green)" radius={[3,3,0,0]}/><ChartTooltip content={<ChartTooltipContent/>}/></BarChart></ChartContainer>;
}

export function ParticipationChart({finding}:{finding:Finding}){
  const data=[{window:"15s",dollars:finding.dollar_volume_15s??0,trades:finding.trades_15s??0,rvol:finding.vol_ratio_15s??0},{window:"30s",dollars:finding.dollar_volume_30s??0,trades:finding.trades_30s??0,rvol:finding.vol_ratio_30s??0}];
  return <ChartContainer className="h-[135px]" config={{dollars:{color:"var(--cyan)"},trades:{color:"var(--blue)"},rvol:{color:"var(--orange)"}}}><ComposedChart data={data} margin={{top:8,right:8,bottom:0,left:-16}}><CartesianGrid vertical={false} stroke="rgba(139,159,181,.08)"/><XAxis dataKey="window" tick={axis} axisLine={false} tickLine={false}/><YAxis tick={axis} axisLine={false} tickLine={false}/><Area type="monotone" dataKey="dollars" name="$ volume" stroke="var(--cyan)" fill="var(--cyan)" fillOpacity={.12}/><Line type="monotone" dataKey="trades" name="Trades" stroke="var(--blue)" dot={{r:2}}/><Line type="monotone" dataKey="rvol" name="RVOL" stroke="var(--orange)" dot={{r:2}}/><ChartTooltip content={<ChartTooltipContent/>}/></ComposedChart></ChartContainer>;
}

export function ValidationOutcomeChart({rows}:{rows:ValidationRow[]}){
  const avg=(values:Array<number|null>)=>{const usable=values.filter((v):v is number=>v!=null);return usable.length?usable.reduce((a,b)=>a+b,0)/usable.length:0;};
  const data=[{window:"Scout",move:avg(rows.map(r=>r.move_at_detection_pct))},{window:"+1m",move:avg(rows.map(r=>r.max_1m_pct))},{window:"+5m",move:avg(rows.map(r=>r.max_5m_pct))},{window:"+15m",move:avg(rows.map(r=>r.max_15m_pct))},{window:"Session",move:avg(rows.map(r=>r.max_session_pct))}];
  return <ChartContainer className="h-[145px]" config={{move:{color:"var(--green)"}}}><AreaChart data={data} margin={{top:8,right:12,bottom:0,left:-18}}><CartesianGrid vertical={false} stroke="rgba(139,159,181,.08)"/><XAxis dataKey="window" tick={axis} axisLine={false} tickLine={false}/><YAxis tick={axis} axisLine={false} tickLine={false}/><Area type="monotone" dataKey="move" name="Average max move %" stroke="var(--green)" fill="var(--green)" fillOpacity={.14}/><ChartTooltip content={<ChartTooltipContent/>}/></AreaChart></ChartContainer>;
}

export function TimeOfDayOutcomeChart({rows}:{rows:ValidationRow[]}){
  const buckets=new Map<string,{label:string;signals:number;follow:number}>();
  for(const row of rows){
    const date=new Date(row.detected_at*1000);
    const minute=Math.floor(date.getMinutes()/30)*30;
    const key=`${String(date.getHours()).padStart(2,"0")}:${String(minute).padStart(2,"0")}`;
    const entry=buckets.get(key)||{label:key,signals:0,follow:0};
    entry.signals+=1;
    if((row.max_5m_pct??0)>=5)entry.follow+=1;
    buckets.set(key,entry);
  }
  const data=[...buckets.values()].sort((a,b)=>a.label.localeCompare(b.label)).slice(-12).map(x=>({...x,followThrough:x.signals?Math.round(x.follow/x.signals*100):0}));
  if(!data.length)return null;
  return <ChartContainer className="h-[145px]" config={{signals:{color:"var(--blue)"},followThrough:{color:"var(--green)"}}}><ComposedChart data={data} margin={{top:8,right:8,bottom:0,left:-18}}><CartesianGrid vertical={false} stroke="rgba(139,159,181,.08)"/><XAxis dataKey="label" tick={axis} axisLine={false} tickLine={false}/><YAxis tick={axis} axisLine={false} tickLine={false}/><Bar dataKey="signals" name="Signals" fill="var(--blue)" fillOpacity={.3} radius={[3,3,0,0]}/><Line dataKey="followThrough" name="5m follow-through %" stroke="var(--green)" strokeWidth={2} dot={{r:2}}/><ChartTooltip content={<ChartTooltipContent/>}/></ComposedChart></ChartContainer>;
}
