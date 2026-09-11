export function downloadCsv(filename: string, headers: string[], rows: (string | number | boolean | null | undefined)[][]) {
  const esc = (v: string | number | boolean | null | undefined) => {
    if (v == null) return "";
    const s = String(v);
    return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const ts = new Date().toISOString().slice(0, 19).replace("T", "_").replace(/:/g, "");
  const stamped = filename.replace(/\.csv$/i, "") + `_${ts}.csv`;
  const csv = [headers.map(esc).join(","), ...rows.map(r => r.map(esc).join(","))].join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
    download: stamped,
  });
  a.click();
  URL.revokeObjectURL(a.href);
}
