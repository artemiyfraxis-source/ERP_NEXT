export function DocumentIcon({ kind, visible = true }: { kind: "connections" | "bom" | "cut" | "positions" | "visibility" | "dimensions" | "volume"; visible?: boolean }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "connections" ? <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M10 9v12"/></>
      : kind === "bom" ? <><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></>
      : kind === "cut" ? <><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8.2 8.2 12 12M8.2 15.8 20 4M13 12l2 2"/></>
      : kind === "positions" ? <><circle cx="9" cy="8" r="5"/><path d="M9 6v4M12.5 11.5 19 18h3"/></>
      : kind === "visibility" ? <><path d="M2 12s3.2-6 10-6 10 6 10 6-3.2 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>{!visible&&<path d="m4 4 16 16"/>}</>
      : kind === "dimensions" ? <><path d="M4 5v14M20 5v14M4 12h16M7 8l-3 4 3 4M17 8l3 4-3 4"/></>
      : <><path d="M4 9v6h3l5 4V5L7 9H4Z"/><path d="M16 9.5a4 4 0 0 1 0 5M18.5 7a7 7 0 0 1 0 10"/></>}
  </svg>;
}
