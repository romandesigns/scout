import type {
  Catalyst,
  Diagnostic,
  Finding,
  Gainer,
  Halt,
  MarketSnapshot,
  NotificationPreferences,
  ScoutStatus,
  ScannerSettings,
  TimelineItem,
  ValidationRow,
  AttentionItem,
  AttentionStatus,
  FindingVerification,
  PushConfig,
} from "./types";

const configured = process.env.NEXT_PUBLIC_SCOUT_API_BASE?.replace(/\/$/, "");
const sameOrigin = process.env.NEXT_PUBLIC_SCOUT_SAME_ORIGIN === "1";
export const API_CONFIGURED = Boolean(configured) || sameOrigin;
export const API_BASE = configured ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export async function getStatus(): Promise<ScoutStatus> {
  return getJson<ScoutStatus>("/api/status");
}

export async function getFindings(limit = 100): Promise<Finding[]> {
  const payload = await getJson<{ items: Finding[] }>(`/api/findings?limit=${limit}&episodes=1`);
  return payload.items;
}

export async function getFinding(id: number): Promise<Finding> {
  return getJson<Finding>(`/api/findings/${id}`);
}

export async function getFindingVerification(id:number):Promise<FindingVerification> {
  return getJson<FindingVerification>(`/api/findings/${id}/verification`);
}

export async function saveFindingReview(id:number,value:{user_grade?:number|null;user_agrees?:boolean|null;reason_tags?:string[];notes?:string}):Promise<FindingVerification> {
  const response=await fetch(`${API_BASE}/api/findings/${id}/review`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
  if(!response.ok)throw new Error(await response.text()||`${response.status} ${response.statusText}`);
  return response.json();
}

export async function getCatalysts(limit = 100): Promise<Catalyst[]> {
  const payload = await getJson<{ items: Catalyst[] }>(`/api/catalysts?limit=${limit}`);
  return payload.items;
}

export async function getGainers(top = 20): Promise<Gainer[]> {
  const payload = await getJson<{ items: Gainer[] }>(`/api/market/gainers?top=${top}`);
  return payload.items;
}

export async function getHalts(): Promise<{ active: Halt[]; recent: Halt[] }> {
  return getJson("/api/market/halts");
}

export async function getMarketSnapshot(ticker: string, detectedAt?: number, bucketSeconds = 15): Promise<MarketSnapshot> {
  const query = new URLSearchParams();
  if (detectedAt) query.set("detected_at", String(detectedAt));
  query.set("bucket_seconds", String(bucketSeconds));
  return getJson<MarketSnapshot>(`/api/market/snapshot/${encodeURIComponent(ticker)}?${query.toString()}`);
}

export async function getDiagnostic(ticker: string): Promise<Diagnostic> {
  return getJson<Diagnostic>(`/api/market/diagnostics/${encodeURIComponent(ticker)}`);
}

export async function getValidation(limit = 100): Promise<ValidationRow[]> {
  const payload = await getJson<{ items: ValidationRow[] }>(`/api/validation?limit=${limit}`);
  return payload.items;
}

export async function getTimeline(ticker?: string, limit = 100): Promise<TimelineItem[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (ticker) query.set("ticker", ticker);
  const payload = await getJson<{ items: TimelineItem[] }>(`/api/timeline?${query.toString()}`);
  return payload.items;
}

export async function getNotificationPreferences(): Promise<NotificationPreferences> {
  return getJson("/api/notifications/preferences");
}

export async function saveNotificationPreferences(value: NotificationPreferences): Promise<NotificationPreferences> {
  const response = await fetch(`${API_BASE}/api/notifications/preferences`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

export async function testNotification(platform: "android" | "windows" | "email") {
  const response = await fetch(`${API_BASE}/api/notifications/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform }),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<{ ok: boolean; platform: string; message: string; native?: boolean }>;
}

export async function getPushConfig():Promise<PushConfig>{
  return getJson<PushConfig>("/api/push/config");
}

export async function registerPushSubscription(subscription:PushSubscriptionJSON){
  const response=await fetch(`${API_BASE}/api/push/subscriptions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({subscription})});
  if(!response.ok)throw new Error(await response.text()||`${response.status} ${response.statusText}`);
  return response.json() as Promise<{ok:boolean}>;
}

export async function removePushSubscription(endpoint:string){
  const response=await fetch(`${API_BASE}/api/push/subscriptions`,{method:"DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({endpoint})});
  if(!response.ok)throw new Error(await response.text()||`${response.status} ${response.statusText}`);
  return response.json() as Promise<{ok:boolean;removed:boolean}>;
}

export async function getScannerSettings(): Promise<ScannerSettings> {
  return getJson<ScannerSettings>("/api/settings/scanner");
}

export async function saveScannerSettings(value: ScannerSettings): Promise<ScannerSettings> {
  const response = await fetch(`${API_BASE}/api/settings/scanner`, {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
  if(!response.ok) throw new Error(await response.text() || `${response.status} ${response.statusText}`);
  return response.json();
}

export async function getAttention(limit=100):Promise<AttentionItem[]> {
  const payload=await getJson<{items:AttentionItem[]}>(`/api/attention?limit=${limit}`);
  return payload.items;
}

export async function updateAttention(id:number,status:AttentionStatus):Promise<AttentionItem> {
  const response=await fetch(`${API_BASE}/api/attention/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status})});
  if(!response.ok)throw new Error(await response.text()||`${response.status} ${response.statusText}`);
  return response.json();
}
