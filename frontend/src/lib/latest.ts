import { useMemo, useRef } from "react";

export interface Latest {
  /** Start a request; `ok` / `fail` run only if no newer request (or cancel) came after it. */
  run<T>(request: Promise<T>, ok: (value: T) => void, fail?: (error: unknown) => void): void;
  /** Forget whatever is in flight, e.g. when the view is reset and no request replaces it. */
  cancel(): void;
}

/** One request stream: a list that reloads when its filters change, or a poll. Only the most
 *  recent request may write state, so an older response arriving late never overwrites fresher
 *  state. Nothing is returned to await, so no caller can hang on a dropped response. */
export function useLatest(): Latest {
  const seq = useRef(0);
  return useMemo(
    () => ({
      run(request, ok, fail) {
        const id = ++seq.current;
        request.then(
          (v) => {
            if (id === seq.current) ok(v);
          },
          (e) => {
            if (id === seq.current) fail?.(e);
          },
        );
      },
      cancel() {
        seq.current++;
      },
    }),
    [],
  );
}

export const errorText = (e: unknown) => String((e as Error)?.message ?? e);
