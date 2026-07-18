/**
 * @finos/open-resource-broker — TypeScript SDK
 *
 * Public API entry point.
 */

export { OrbClient } from "./client.js";
export type {
  ClientConfig,
  StreamEvent,
} from "./client.js";
export type { AuthOption } from "./auth/index.js";
export type { ProcessConfig } from "./process/manager.js";
export { tempSocketPath } from "./process/manager.js";
export {
  OrbError,
  OrbApiError,
  OrbUnauthorizedError,
  OrbForbiddenError,
  OrbNotFoundError,
  OrbConflictError,
  OrbTimeoutError,
  OrbUnavailableError,
  apiErrorForStatus,
} from "./errors.js";
export type { ApiErrorInit } from "./errors.js";
export type {
  SseFrame,
  OrbSsePayload,
  OrbSseRequest,
  OrbMachine,
} from "./sse/reader.js";
export {
  isSentinel,
  parseOrbPayload,
  TERMINAL_STATUSES,
  MAX_SSE_FRAME_BYTES,
  SseFrameTooLargeError,
} from "./sse/reader.js";

// Re-export generated models for consumers who need the raw types
export type {
  TemplateItem,
  TemplateListResponse,
  TemplateCreateRequest,
  TemplateUpdateRequest,
  TemplateMutationResponse,
  MachineItem,
  MachineListResponse,
  MachineReferenceDTO,
  RequestItem,
  RequestStatusResponse,
  RequestOperationResponse,
  RequestMachinesRequest as RequestMachinesBody,
  RequestMachinesRequest,
  ReturnMachinesRequest,
  BatchRequestStatusBody,
  InitBody,
  CleanupDatabaseBody,
  GenerateTemplatesBody,
  SaveRequest,
  SetValueRequest,
} from "../generated/models/index.js";
