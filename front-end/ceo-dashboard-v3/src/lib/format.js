export function ils(n) {
  const v = Number(n || 0);
  return "₪" + v.toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function num(n) {
  return Number(n || 0).toLocaleString("en");
}

export function dt(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("en-GB", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch (_) { return iso; }
}

// Map a backend readiness mode to a status color token.
export function readinessColor(readiness) {
  return { production: "ok", sandbox: "warn", mock: "warn", stub: "muted" }[readiness] || "muted";
}
