import type { ReactNode } from "react";
import type { EvidenceSpan } from "./api";
import { sectionStyle } from "./format";

/** DOM id of a quote's first highlighted piece; the evidence list scrolls to it. */
export const spanId = (s: EvidenceSpan) => `ev-${s.field}-${s.start}-${s.end}-${s.section}`;

/** Text with every evidence span marked. Overlapping quotes (two sections citing the same words)
 *  are split into segments, so each quote stays visible: a segment covered by several quotes takes
 *  the first one's colour, and every section that ends there is labelled. */
export function highlight(text: string, spans: EvidenceSpan[], sectionIndex: (id: string) => number): ReactNode[] {
  const uniq = [...new Map(spans.filter((s) => s.end > s.start).map((s) => [spanId(s), s])).values()];
  if (!uniq.length) return [text];
  const cuts = [...new Set([0, text.length, ...uniq.flatMap((s) => [s.start, s.end])])].sort((a, b) => a - b);
  const out: ReactNode[] = [];
  const placed = new Set<string>();
  for (let i = 0; i < cuts.length - 1; i++) {
    const [a, b] = [cuts[i], cuts[i + 1]];
    const piece = text.slice(a, b);
    const cover = uniq.filter((s) => s.start <= a && s.end >= b).sort((x, y) => x.start - y.start || y.end - x.end);
    if (!cover.length) {
      out.push(piece);
      continue;
    }
    const first = cover.find((s) => !placed.has(spanId(s)) && s.start === a);
    if (first) placed.add(spanId(first));
    cover.filter((s) => s.start === a && s !== first).forEach((s) => placed.add(spanId(s)));
    const ending = [...new Set(cover.filter((s) => s.end === b).map((s) => s.section))];
    out.push(
      <mark
        key={a}
        className="ev"
        id={first ? spanId(first) : undefined}
        data-also={cover
          .filter((s) => s.start === a && s !== first)
          .map(spanId)
          .join(" ") || undefined}
        // Colour of the innermost quote covering this piece, so a quote nested in another stands out.
        style={sectionStyle(sectionIndex([...cover].sort((x, y) => x.end - x.start - (y.end - y.start))[0].section))}
      >
        {piece}
        {ending.length > 0 && <sup>{ending.join(" ")}</sup>}
      </mark>,
    );
  }
  return out;
}
