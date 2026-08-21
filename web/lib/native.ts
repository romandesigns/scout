import type { Finding, NotificationPreferences } from "./types";

const CRITICAL = new Set(["FIRST_LEG", "SURGE", "IGNITION", "HALT_PRESSURE", "CATALYST_ACTIVE", "HALT"]);
const SETUP = new Set(["EARLY"]);
const CONFIRMED = new Set(["IGNITION", "BREAKOUT", "SURGE"]);
const SPECIAL = new Set(["CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME", "HALT_WATCH", "HALT_PRESSURE"]);
const USER_NOTIFY = new Set([...SETUP, ...CONFIRMED, ...SPECIAL]);
const claimedDecisions = new Set<string>();
const nativePending = new Map<string, { finding: Finding; prefs: NotificationPreferences; timer: ReturnType<typeof setTimeout> }>();
const stagePriority: Record<string,number> = { ACTIVITY_WATCH:0, REVERSAL_WATCH:0, FIRST_LEG_WATCH:0, PRE_IGNITION:0, EARLY:2, STAIRCASE:2, FIRST_LEG:3, EMA_RECLAIM:3, SURGE:4, VWAP_RECLAIM:4, BREAKOUT:5, REARM:6, IGNITION:7, CATALYST_WATCH:8, CATALYST_ACTIVE:10, RESUME:9, HALT:10 };

function isCritical(finding: Finding) {
  return Array.from(new Set([finding.stage, ...(finding.signals || [])])).some((signal) => CRITICAL.has(signal));
}

function targetPlatform() {
  if (typeof navigator === "undefined") return "windows" as const;
  return /android/i.test(navigator.userAgent) ? "android" as const : "windows" as const;
}

// Distinguishes "installed to home screen" (real app-like context, standalone display, no
// browser chrome) from a plain browser tab -- the split the 2026-08-19 notification design
// actually keys off of, since installed-vs-not matters more here than Android-vs-iPhone
// (both installed platforms use the same Web Push mechanism).
export function isInstalledPwa(): boolean {
  if (typeof window === "undefined") return false;
  const standaloneMedia = window.matchMedia?.("(display-mode: standalone)").matches;
  const iosStandalone = (navigator as unknown as { standalone?: boolean }).standalone === true;
  return Boolean(standaloneMedia || iosStandalone);
}

function priorityName(value?: string) {
  return (["low", "normal", "high", "critical"].includes(String(value).toLowerCase())
    ? String(value).toLowerCase()
    : "high") as "low" | "normal" | "high" | "critical";
}


function signalMode(finding: Finding, prefs: NotificationPreferences) {
  const signals = Array.from(new Set([finding.stage, ...(finding.signals || [])]));
  const modes = signals.map((signal) => prefs.signals[signal] ?? "notify");
  if (modes.includes("notify")) return "notify";
  if (modes.includes("silent")) return "silent";
  return "off";
}

function marketClock(ts: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(new Date(ts * 1000));
  const hour = Number(parts.find((p) => p.type === "hour")?.value || 0) % 24;
  const minute = Number(parts.find((p) => p.type === "minute")?.value || 0);
  return hour * 60 + minute;
}

function sessionFor(ts: number) {
  const minutes = marketClock(ts);
  if (minutes >= 20 * 60 || minutes < 4 * 60) return "overnight";
  if (minutes < 9 * 60 + 30) return "premarket";
  if (minutes < 16 * 60) return "regular";
  return "afterhours";
}

function quietNow(finding: Finding, prefs: NotificationPreferences) {
  const quiet = prefs.quiet_hours;
  if (!quiet.enabled) return false;
  const parse = (value: string) => {
    const [h, m] = value.split(":").map(Number);
    return (Number.isFinite(h) ? h : 0) * 60 + (Number.isFinite(m) ? m : 0);
  };
  const now = marketClock(finding.detected_at);
  const start = parse(quiet.start);
  const end = parse(quiet.end);
  const inside = start < end ? now >= start && now < end : now >= start || now < end;
  if (!inside) return false;
  if (quiet.allow_critical && isCritical(finding)) return false;
  return true;
}

// Shared gates for every client-side delivery path (Tauri desktop toast, PWA foreground
// toast, plain-browser shadcn toast) -- 2026-08-19 refactor mirrors the same split made
// server-side in app/notifiers.py for Web Push: platform-agnostic checks once, then each
// caller applies its own platform-specific toggle on top, instead of one function silently
// assuming a single target platform for every client.
function coreAllowed(finding: Finding, prefs: NotificationPreferences) {
  if (!USER_NOTIFY.has(finding.stage)) return false;
  if (!["CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME"].includes(finding.stage) && finding.quality_label !== "CLEAN") return false;
  if (!prefs.master_enabled) return false;
  if (signalMode(finding, prefs) !== "notify") return false;
  if (finding.score < prefs.minimum_score) return false;
  if (!prefs.sessions[sessionFor(finding.detected_at)]) return false;
  if (quietNow(finding, prefs)) return false;
  return true;
}

function decisionPhase(finding: Finding) {
  if (SETUP.has(finding.stage)) return "setup";
  if (CONFIRMED.has(finding.stage)) return "confirmed";
  return finding.stage.toLowerCase();
}

export function claimClientDecision(finding: Finding) {
  const phase = decisionPhase(finding);
  if (phase !== "setup" && phase !== "confirmed") return true;
  const episode = finding.hybrid_key || `${finding.ticker}:${finding.episode_id || 0}`;
  const key = `${episode}:${phase}`;
  if (claimedDecisions.has(key)) return false;
  claimedDecisions.add(key);
  return true;
}

function decisionTitle(finding: Finding) {
  if (SETUP.has(finding.stage)) return `${finding.ticker} · BULLISH SETUP`;
  if (CONFIRMED.has(finding.stage)) return `${finding.ticker} · MOMENTUM CONFIRMED`;
  return `${finding.ticker} · ${finding.stage.replaceAll("_", " ")}`;
}

function decisionBody(finding: Finding) {
  const price = finding.price ? (finding.price < 1 ? `$${finding.price.toFixed(4)}` : `$${finding.price.toFixed(2)}`) : "";
  const trigger = finding.trigger_level ?? finding.breakout_level;
  const invalidation = finding.invalidation_level;
  if (SETUP.has(finding.stage)) {
    const distance = trigger && finding.price ? (trigger / finding.price - 1) * 100 : finding.trigger_distance_pct;
    return [
      price,
      trigger != null ? `trigger $${trigger.toFixed(4)}${distance != null ? ` (${distance >= 0 ? "+" : ""}${distance.toFixed(2)}%)` : ""}` : "trigger forming",
      invalidation != null ? `invalid below $${invalidation.toFixed(4)}` : "invalid on structure/VWAP loss",
      `${finding.actionable_rank || "C"}-rank ${String(finding.quality_label || "developing").toLowerCase()}`,
      "Scout monitoring",
    ].filter(Boolean).join(" · ");
  }
  return [
    `confirmed ${price}`,
    trigger != null ? `through $${trigger.toFixed(4)}` : "momentum confirmed",
    invalidation != null ? `invalid below $${invalidation.toFixed(4)}` : "",
    `${String(finding.quality_label || "developing").toLowerCase()} quality`,
  ].filter(Boolean).join(" · ");
}

function nativeAllowed(finding: Finding, prefs: NotificationPreferences) {
  if (!coreAllowed(finding, prefs)) return false;
  const platform = targetPlatform();
  if (!prefs.platforms[platform].enabled) return false;
  if (platform === "windows" && !prefs.platforms.windows.toast) return false;
  return true;
}

// PWA installed on a phone (Android or iPhone) but not running inside Tauri -- gated by the
// same "android" preference bucket the server uses for Web Push background delivery, so the
// foreground and background experience agree with each other.
export function webPushForegroundAllowed(finding: Finding, prefs: NotificationPreferences) {
  if (!coreAllowed(finding, prefs)) return false;
  return prefs.platforms.android.enabled;
}

// Plain browser tab: not installed, not Tauri. No platform bucket applies (there isn't a
// "web" toggle in the preference schema, deliberately -- see app/notifiers.py's
// notification_allowed_any_platform for the server-side equivalent reasoning).
export function webToastAllowed(finding: Finding, prefs: NotificationPreferences) {
  return coreAllowed(finding, prefs);
}

export async function syncNativeNotificationChannels(prefs: NotificationPreferences) {
  try {
    const { isTauri } = await import("@tauri-apps/api/core");
    if (!isTauri() || targetPlatform() !== "android") return false;
    const { channels, createChannel, removeChannel, Importance, Visibility } = await import("@tauri-apps/plugin-notification");
    const owned = (await channels()).filter((channel) => channel.id.startsWith("scout-"));
    for (const channel of owned) {
      try { await removeChannel(channel.id); } catch { /* OS can retain user-managed channels. */ }
    }
    const basePriority = priorityName(prefs.platforms.android.priority);
    const baseImportance = basePriority === "low" ? Importance.Low : basePriority === "normal" ? Importance.Default : Importance.High;
    await createChannel({
      id: "scout-critical",
      name: "Scout critical signals",
      description: "Immediate surge, ignition, and trading halt alerts",
      importance: Importance.High,
      vibration: prefs.platforms.android.vibration,
      visibility: Visibility.Private,
    });
    await createChannel({
      id: "scout-default",
      name: "Scout market signals",
      description: "Early, breakout, staircase, catalyst, resume, and re-arm alerts",
      importance: baseImportance,
      vibration: prefs.platforms.android.vibration,
      visibility: Visibility.Private,
    });
    return true;
  } catch {
    return false;
  }
}

export async function sendNativeScoutNotification(finding: Finding, prefs: NotificationPreferences) {
  if (!nativeAllowed(finding, prefs)) return false;
  const platform = targetPlatform();

  try {
    const { isTauri } = await import("@tauri-apps/api/core");
    if (!isTauri()) return false;
    const { isPermissionGranted, requestPermission, sendNotification } = await import("@tauri-apps/plugin-notification");
    let granted = await isPermissionGranted();
    if (!granted) granted = (await requestPermission()) === "granted";
    if (!granted) return false;

    sendNotification({
      id: Math.abs(Number(finding.id || 0)) % 2_147_483_647 || undefined,
      title: decisionTitle(finding),
      body: decisionBody(finding),
      group: prefs.group_by_ticker ? finding.ticker : undefined,
      channelId: platform === "android" ? (isCritical(finding) ? "scout-critical" : "scout-default") : undefined,
      actionTypeId: platform === "android" ? "scout-finding" : undefined,
      autoCancel: true,
      extra: {
        finding: String(finding.id),
        ticker: finding.ticker,
        deepLink: `stockhunter-scout://finding?finding=${finding.id}&ticker=${encodeURIComponent(finding.ticker)}`,
      },
    });
    return true;
  } catch {
    // Browser builds intentionally fall back to server-side ntfy/email delivery.
    return false;
  }
}

// Installed PWA (Android or iPhone), app currently open/foregrounded. Background delivery
// while closed already works via Web Push (lib/web-push.ts + public/sw.js's `push` handler).
// This covers the other half: while the tab has focus, browsers commonly suppress the OS
// push banner for the page that's already visible, which would otherwise make the PWA feel
// silent/non-native compared to a real installed app. Calling showNotification() directly
// on the active service worker registration produces the same native OS notification (icon,
// vibrate, actions) the background push path does, so the experience is consistent whether
// the app is open or closed -- the "feel like any other mobile app" requirement.
export async function showPwaForegroundNotification(finding: Finding) {
  try {
    if (!("serviceWorker" in navigator) || !("Notification" in window)) return false;
    let granted = Notification.permission === "granted";
    if (Notification.permission === "default") granted = (await Notification.requestPermission()) === "granted";
    if (!granted) return false;
    const registration = await navigator.serviceWorker.ready;
    const critical = isCritical(finding);
    // renotify/vibrate are real, widely-supported Notification API options (used already by
    // public/sw.js's push handler) that TS's default DOM lib type doesn't declare.
    const options = {
      body: decisionBody(finding),
      icon: "/icons/scout-192.png", badge: "/icons/scout-192.png",
      tag: `scout-${finding.ticker}`, renotify: critical,
      requireInteraction: critical, vibrate: critical ? [180, 80, 180] : [120],
      data: { url: `/?finding=${finding.id}&ticker=${encodeURIComponent(finding.ticker)}` },
    } as NotificationOptions & { renotify?: boolean; vibrate?: number[] };
    await registration.showNotification(decisionTitle(finding), options);
    return true;
  } catch {
    return false;
  }
}

export function queueNativeScoutNotification(finding: Finding, prefs: NotificationPreferences) {
  if (["HALT","RESUME","CATALYST","CATALYST_WATCH","CATALYST_ACTIVE"].includes(finding.stage)) {
    void sendNativeScoutNotification(finding,prefs);
    return;
  }
  const ticker=finding.ticker.toUpperCase();
  const current=nativePending.get(ticker);
  if (current) {
    if ((stagePriority[finding.stage]??1) >= (stagePriority[current.finding.stage]??1)) {
      current.finding=finding;
      current.prefs=prefs;
    }
    return;
  }
  const pending={finding,prefs,timer:setTimeout(()=>{
    const latest=nativePending.get(ticker);
    nativePending.delete(ticker);
    if(latest && claimClientDecision(latest.finding)) void sendNativeScoutNotification(latest.finding,latest.prefs);
  },finding.stage==="FIRST_LEG"?3000:8000)};
  nativePending.set(ticker,pending);
}

export async function sendNativeTestNotification(requestedPlatform?: "windows" | "android") {
  try {
    const { isTauri } = await import("@tauri-apps/api/core");
    if (!isTauri()) return false;
    const current = targetPlatform();
    if (requestedPlatform && requestedPlatform !== current) return false;
    const { isPermissionGranted, requestPermission, sendNotification } = await import("@tauri-apps/plugin-notification");
    let granted = await isPermissionGranted();
    if (!granted) granted = (await requestPermission()) === "granted";
    if (!granted) return false;
    sendNotification({ title: "Scout notification test", body: "Native Scout notifications are enabled on this device.", channelId: current === "android" ? "scout-default" : undefined });
    return true;
  } catch {
    return false;
  }
}


export async function getNativeAutostartState(): Promise<{ supported: boolean; enabled: boolean }> {
  try {
    const { isTauri } = await import("@tauri-apps/api/core");
    if (!isTauri() || targetPlatform() !== "windows") return { supported: false, enabled: false };
    const { isEnabled } = await import("@tauri-apps/plugin-autostart");
    return { supported: true, enabled: await isEnabled() };
  } catch {
    return { supported: false, enabled: false };
  }
}

export async function setNativeAutostart(enabled: boolean): Promise<boolean> {
  try {
    const { isTauri } = await import("@tauri-apps/api/core");
    if (!isTauri() || targetPlatform() !== "windows") return false;
    const { enable, disable } = await import("@tauri-apps/plugin-autostart");
    if (enabled) await enable();
    else await disable();
    return true;
  } catch {
    return false;
  }
}

export function installNativeNotificationActionHandler(onUrl: (url: string) => void) {
  let disposed = false;
  let listener: { unregister: () => Promise<void> } | undefined;

  void (async () => {
    try {
      const { isTauri } = await import("@tauri-apps/api/core");
      if (!isTauri() || targetPlatform() !== "android" || disposed) return;
      const { registerActionTypes, onAction } = await import("@tauri-apps/plugin-notification");
      await registerActionTypes([{
        id: "scout-finding",
        actions: [{ id: "view", title: "View Scout", foreground: true }],
      }]);
      listener = await onAction((notification) => {
        const deepLink = notification.extra?.deepLink;
        if (typeof deepLink === "string") onUrl(deepLink);
      });
      if (disposed) void listener.unregister().catch(() => undefined);
    } catch {
      // Mobile actions are supplemental; normal deep links/ntfy remain available.
    }
  })();

  return () => {
    disposed = true;
    if (listener) void listener.unregister().catch(() => undefined);
  };
}

export function installNativeDeepLinkHandler(onUrl: (url: string) => void) {
  let disposed = false;
  let unlisten: (() => void) | undefined;

  void (async () => {
    try {
      const { isTauri } = await import("@tauri-apps/api/core");
      if (!isTauri() || disposed) return;
      const { getCurrent, onOpenUrl } = await import("@tauri-apps/plugin-deep-link");
      const current = await getCurrent();
      for (const url of current || []) onUrl(url);
      unlisten = await onOpenUrl((urls) => {
        for (const url of urls) onUrl(url);
      });
      if (disposed) unlisten?.();
    } catch {
      // Standard browser builds use URL query parameters directly.
    }
  })();

  return () => {
    disposed = true;
    unlisten?.();
  };
}
