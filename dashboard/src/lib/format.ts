export function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`;
}

export function secs(x: number | null | undefined): string {
  if (x === null || x === undefined) return "–";
  return x < 60 ? `${x.toFixed(1)}s` : `${Math.floor(x / 60)}m ${Math.round(x % 60)}s`;
}

export function usd(x: number | null | undefined): string {
  if (x === null || x === undefined) return "–";
  if (x === 0) return "$0";
  return x < 0.01 ? `$${x.toFixed(4)}` : `$${x.toFixed(2)}`;
}

export function when(iso: string, now: Date = new Date()): string {
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "{"element": 3}" → "element: 3" for compact display of tool arguments. */
export function prettyArgs(args: string | null): string {
  if (!args) return "";
  try {
    const parsed = JSON.parse(args) as Record<string, unknown>;
    return Object.entries(parsed)
      .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(", ");
  } catch {
    return args;
  }
}
