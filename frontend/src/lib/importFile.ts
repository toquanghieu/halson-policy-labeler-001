import type { Posting } from "./api";

/** RFC 4180 CSV: quoted fields, doubled quotes, newlines inside quotes (EMSCAD has them). */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      if (row.some((c) => c !== "")) rows.push(row);
      row = [];
    } else field += ch;
  }
  row.push(field);
  if (row.some((c) => c !== "")) rows.push(row);
  const [header, ...body] = rows;
  if (!header) return [];
  const keys = header.map((h) => h.trim());
  return body.map((r) => Object.fromEntries(keys.map((k, i) => [k, r[i] ?? ""])));
}

const TEXT = [
  "title", "location", "department", "salary_range", "company_profile", "description", "requirements",
  "benefits", "employment_type", "required_experience", "required_education", "industry", "function",
] as const;
const BOOL = ["telecommuting", "has_company_logo", "has_questions"] as const;

/** Map one record (CSV row or JSON object) to a posting. Unknown keys, including a dataset
 *  `fraudulent` column, are ignored: imported postings carry no gold label. */
export function toPosting(rec: Record<string, unknown>, fallbackId: string): Partial<Posting> {
  const p: Record<string, unknown> = { job_id: String(rec.job_id ?? "").trim() || fallbackId };
  for (const k of TEXT) if (rec[k] !== undefined && rec[k] !== null) p[k] = String(rec[k]);
  for (const k of BOOL) {
    const v = rec[k];
    if (v !== undefined && v !== "") p[k] = v === true || v === 1 || ["1", "true", "yes", "t"].includes(String(v).toLowerCase());
  }
  return p as Partial<Posting>;
}

/** JSON (one object, an array, or {"postings": [...]}) or CSV with EMSCAD's column names. */
export function parseUpload(text: string, name = ""): Partial<Posting>[] {
  const stamp = Date.now().toString(36);
  const trimmed = text.trim();
  if (!trimmed) return [];
  let records: Record<string, unknown>[];
  if (name.toLowerCase().endsWith(".csv") || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    records = parseCsv(trimmed);
  } else {
    const data = JSON.parse(trimmed);
    records = Array.isArray(data) ? data : Array.isArray(data.postings) ? data.postings : [data];
  }
  return records.map((r, i) => toPosting(r, `imp-${stamp}-${i + 1}`));
}
