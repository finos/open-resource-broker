import { OrbStreamError, OrbStreamTimeoutError } from "../errors.js";
import type { SseFrame } from "./reader.js";

/**
 * Raise the error announced by a named outcome frame of a request status
 * stream. `timeout` and `error` frames end the stream without the request being
 * finished; `complete` frames and plain data frames (including those from
 * servers that predate named frames) are not errors.
 */
export function throwOnStreamOutcome(frame: SseFrame): void {
  if (frame.event === "timeout") throw new OrbStreamTimeoutError();
  if (frame.event === "error") throw new OrbStreamError();
}
