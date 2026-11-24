import type { KeyboardEvent } from "react";

/** Props for a non-button element that opens something on click: reachable with Tab and
 *  activated with Enter or Space, like a button. */
export function clickable(onActivate: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate();
      }
    },
  };
}
