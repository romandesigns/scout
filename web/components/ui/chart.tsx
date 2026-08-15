"use client";

import * as React from "react";
import { ResponsiveContainer, Tooltip } from "recharts";
import { cn } from "@/lib/utils";

export type ChartConfig = Record<string,{label?:React.ReactNode;color?:string}>;

export function ChartContainer({config,className,children}:{config:ChartConfig;className?:string;children:React.ReactElement}) {
  const vars=Object.fromEntries(Object.entries(config).map(([key,value])=>[`--color-${key}`,value.color||"currentColor"]));
  return <div className={cn("scout-chart-container",className)} style={vars as React.CSSProperties}><ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer></div>;
}

export const ChartTooltip=Tooltip;

export function ChartTooltipContent({active,payload,label}:any){
  if(!active||!payload?.length)return null;
  return <div className="scout-chart-tooltip">{label&&<b>{label}</b>}{payload.map((item:any,index:number)=><div key={`${item.name}-${index}`}><i style={{background:item.color}}/><span>{item.name}</span><strong>{typeof item.value==="number"?item.value.toLocaleString():item.value}</strong></div>)}</div>;
}
