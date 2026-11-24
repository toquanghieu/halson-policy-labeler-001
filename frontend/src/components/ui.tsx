import type { ReactNode } from "react";
import type { LabelValue } from "../lib/api";
import { LABEL_TEXT, LOW_CONFIDENCE, sectionStyle } from "../lib/format";
import { usePolicy } from "../lib/policyContext";

export function SectionChip({ id, withName = false }: { id: string; withName?: boolean }) {
  const { sectionIndex, section } = usePolicy();
  const s = section(id);
  return (
    <span className="sec" style={sectionStyle(sectionIndex(id))} title={s ? `${s.id} ${s.name}` : id}>
      {id}
      {withName && s ? <span style={{ fontFamily: "var(--sans)" }}>{s.name}</span> : null}
    </span>
  );
}

export function Stamp({ label, big = false }: { label: LabelValue; big?: boolean }) {
  return <span className={`stamp ${label}${big ? " big" : ""}`}>{LABEL_TEXT[label]}</span>;
}

export function Confidence({ value }: { value: number }) {
  return (
    <span className={`conf${value < LOW_CONFIDENCE ? " low" : ""}`} title="Model confidence after verification penalties">
      <span className="conf-bar">
        <i style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      {value.toFixed(2)}
    </span>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <div className="eyebrow">{children}</div>;
}
