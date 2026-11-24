import { createContext, useContext } from "react";
import type { PolicyDoc, Section } from "./api";

/** The policy version being viewed. Components look sections up here, so a section
 *  added in a new version gets its name and color with no UI change. */
export interface PolicyCtx {
  policy: PolicyDoc | null;
  sectionIndex: (id: string) => number;
  section: (id: string) => Section | undefined;
}

export const PolicyContext = createContext<PolicyCtx>({ policy: null, sectionIndex: () => 0, section: () => undefined });
export const usePolicy = () => useContext(PolicyContext);
