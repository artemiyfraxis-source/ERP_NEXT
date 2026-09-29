import { useEffect, useRef, useState } from "react";

// Local auto-update (scripts/update-local.ps1) writes /version.json next to the built site: the version and the PRs
// merged since the previous update. Open tabs poll it; a new version reloads the tab once the user is idle, and
// after the reload a quiet banner lists what changed. No version.json (dev server, server deploy) — nothing shows.
export type SiteVersion = { version: string; date?: string; news?: string[] };

export const VERSION_URL = "/version.json";
const SEEN_KEY = "erp.seenVersion";
const POLL_MS = 60_000;
const IDLE_MS = 30_000;

async function fetchVersion(): Promise<SiteVersion | null> {
  try {
    const response = await fetch(VERSION_URL, { cache: "no-store" });
    if (!response.ok) return null;
    const body = (await response.json()) as SiteVersion;
    return typeof body?.version === "string" ? body : null;
  } catch {
    return null;
  }
}

function readSeen(): string | null {
  try { return window.localStorage.getItem(SEEN_KEY); } catch { return null; }
}

function writeSeen(version: string) {
  try { window.localStorage.setItem(SEEN_KEY, version); } catch { /* private mode: the banner just shows again */ }
}

// Typing into a field or a recent click/keypress means the user is working: do not reload under them.
function isEditing(): boolean {
  const active = document.activeElement;
  if (!active) return false;
  const tag = active.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (active as HTMLElement).isContentEditable;
}

export function UpdateBanner({ pollMs = POLL_MS, idleMs = IDLE_MS }: { pollMs?: number; idleMs?: number }) {
  const [shown, setShown] = useState<SiteVersion | null>(null);
  const loaded = useRef<string | null>(null);
  const lastActivity = useRef(Date.now());

  useEffect(() => {
    const touch = () => { lastActivity.current = Date.now(); };
    const events = ["keydown", "pointerdown", "input", "wheel"] as const;
    events.forEach((name) => window.addEventListener(name, touch, { passive: true }));
    let pending = false;
    let cancelled = false;

    void fetchVersion().then((current) => {
      if (cancelled || !current) return;
      loaded.current = current.version;
      if (readSeen() !== current.version && current.news?.length) setShown(current);
      else writeSeen(current.version);
    });

    // Tick often so a pending reload fires soon after the user goes idle; fetch the version only every pollMs.
    const tickMs = Math.min(pollMs, 5_000);
    let lastPoll = Date.now();
    const tick = window.setInterval(() => {
      if (pending) {
        if (Date.now() - lastActivity.current >= idleMs && !isEditing()) window.location.reload();
        return;
      }
      if (Date.now() - lastPoll < pollMs) return;
      lastPoll = Date.now();
      void fetchVersion().then((next) => {
        if (next && loaded.current && next.version !== loaded.current) pending = true;
      });
    }, tickMs);

    return () => {
      cancelled = true;
      window.clearInterval(tick);
      events.forEach((name) => window.removeEventListener(name, touch));
    };
  }, [pollMs, idleMs]);

  if (!shown) return null;
  const dismiss = () => {
    writeSeen(shown.version);
    setShown(null);
  };
  return (
    <div className="update-banner" role="status">
      <div className="update-banner-body">
        <strong>Обновлено{shown.date ? `: ${shown.date}` : ""}</strong>
        <ul>{shown.news!.map((line) => <li key={line}>{line}</li>)}</ul>
      </div>
      <button className="link-button" onClick={dismiss} aria-label="Скрыть">Понятно</button>
    </div>
  );
}
