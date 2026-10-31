import type { OrbSseRequest } from "./reader.js";

export interface StreamEvent {
  requestId: string;
  status: string;
  message?: string;
  requestedCount?: number;
  successfulCount?: number;
  failedCount?: number;
  machines: Array<{
    machineId: string;
    name?: string;
    status?: string;
    result?: string;
    privateIp?: string;
    publicIp?: string;
    launchTime?: string;
    message?: string;
  }>;
}

/**
 * Convert a request entry of a request status stream frame into a StreamEvent.
 * The server sends machine launch_time as a Unix timestamp number (null while
 * unknown); it is exposed as decimal text, or undefined when unknown.
 */
export function orbSseRequestToStreamEvent(req: OrbSseRequest): StreamEvent {
  return {
    requestId: req.request_id,
    status: req.status,
    message: req.message,
    requestedCount: req.requested_count,
    successfulCount: req.successful_count,
    failedCount: req.failed_count,
    machines: (req.machines ?? []).map((m) => ({
      machineId: m.machine_id,
      name: m.name,
      status: m.status,
      result: m.result,
      privateIp: m.private_ip_address ?? undefined,
      publicIp: m.public_ip_address ?? undefined,
      launchTime:
        m.launch_time === null || m.launch_time === undefined ? undefined : String(m.launch_time),
      message: m.message,
    })),
  };
}
