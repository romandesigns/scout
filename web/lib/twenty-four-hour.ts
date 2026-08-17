import { API_BASE } from "@/lib/api";

export type TwentyFourHourStock = {
  ticker:string; price:number|null; last_feed:"boats"|"sip"|string|null; last_trade_at:number|null; last_boats_trade_at:number;
  session_date:string; verified_24h:boolean; stage:string; actionable_rank:"A"|"B"|"C"; quality_label:string; quality_score:number;
  ross_match:boolean; ross_score:number; change_5s_pct:number|null; change_15s_pct:number|null; change_30s_pct:number|null;
  vol_ratio_15s:number|null; dollar_volume_15s:number|null; trades_15s:number|null; extension_pct:number|null; trigger_distance_pct:number|null;
  rejection_reasons:string[]; latest_finding?:{id:number;ticker:string;stage:string;detected_at:number;price:number;score:number;signals?:string[]}|null;
};

export async function getTwentyFourHourStocks(limit=200):Promise<TwentyFourHourStock[]>{
  const response=await fetch(`${API_BASE}/api/market/24h?limit=${limit}`,{cache:"no-store"});
  if(!response.ok)throw new Error(`${response.status} ${response.statusText}`);
  const payload=await response.json() as {items:TwentyFourHourStock[]};
  return payload.items||[];
}
