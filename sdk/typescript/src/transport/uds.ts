/**
 * Layer 2: UNIX Domain Socket Transport
 *
 * Uses undici's Agent with a socketPath to dial a UNIX socket, then wraps it
 * as a custom Axios adapter so the rest of the SDK can issue ordinary HTTP
 * requests without knowing the underlying channel.
 *
 * Critical note: undici's Dispatcher is abstract — you CANNOT do
 * `new Dispatcher(...)`. Use `new Agent({ connect: { socketPath } })` instead.
 *
 * Streaming support: when the response Content-Type is text/event-stream or
 * the request is configured with responseType: 'stream', the adapter returns
 * a Node.js PassThrough stream instead of buffering the full response.
 */

import { Agent, request as undiciRequest } from "undici";
import { PassThrough, Readable } from "stream";
import type { AxiosAdapter, InternalAxiosRequestConfig, AxiosResponse } from "axios";
import { settle } from "./axios-settle";

// One Agent per socket path; reuse across requests.
const agentCache = new Map<string, Agent>();

function getAgent(socketPath: string): Agent {
  if (!agentCache.has(socketPath)) {
    agentCache.set(
      socketPath,
      new Agent({
        connect: { socketPath },
        pipelining: 0,
        connections: 4,
      })
    );
  }
  return agentCache.get(socketPath)!;
}

/**
 * Build an Axios adapter that routes all requests through a UNIX domain socket.
 * The HTTP host/port in the URL is ignored; all traffic goes to socketPath.
 */
export function makeUdsAdapter(socketPath: string): AxiosAdapter {
  return async function udsAdapter(
    config: InternalAxiosRequestConfig
  ): Promise<AxiosResponse> {
    const url = new URL(
      config.url!,
      config.url?.startsWith("http") ? undefined : "http://localhost"
    );

    const method = (config.method ?? "GET").toUpperCase();

    // Serialize request body
    let body: Buffer | string | undefined;
    if (config.data !== undefined && config.data !== null) {
      body =
        typeof config.data === "string"
          ? config.data
          : JSON.stringify(config.data);
    }

    // Flatten headers
    const headers: Record<string, string> = {};
    if (config.headers) {
      for (const [k, v] of Object.entries(config.headers)) {
        if (v !== undefined && v !== null && typeof v !== "object") {
          headers[k.toLowerCase()] = String(v);
        }
      }
    }
    if (body && !headers["content-type"]) {
      headers["content-type"] = "application/json";
    }

    const agent = getAgent(socketPath);

    const { statusCode, headers: respHeaders, body: respBody } = await undiciRequest(
      url.toString(),
      {
        method: method as
          | "GET"
          | "POST"
          | "PUT"
          | "DELETE"
          | "PATCH"
          | "HEAD"
          | "OPTIONS",
        headers,
        body,
        dispatcher: agent,
      }
    );

    const contentType = String(respHeaders["content-type"] ?? "");
    const isStream =
      config.responseType === "stream" ||
      contentType.includes("text/event-stream");

    let data: unknown;

    if (isStream) {
      // For SSE and streaming responses: pipe to a PassThrough so callers
      // get a proper Node.js Readable that supports destroy()
      const pt = new PassThrough();
      respBody.pipe(pt);
      data = pt;
    } else {
      // Buffer the full response body
      const chunks: Buffer[] = [];
      for await (const chunk of respBody) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const responseData = Buffer.concat(chunks).toString("utf8");

      if (contentType.includes("application/json") && responseData) {
        try {
          data = JSON.parse(responseData);
        } catch {
          data = responseData;
        }
      } else {
        data = responseData;
      }
    }

    const axiosResponse: AxiosResponse = {
      data,
      status: statusCode,
      statusText: String(statusCode),
      headers: respHeaders as Record<string, string>,
      config,
      request: {},
    };

    return new Promise((resolve, reject) => {
      settle(resolve, reject, axiosResponse);
    });
  };
}

/**
 * Close all cached UDS agents. Call during shutdown.
 */
export async function closeAllUdsAgents(): Promise<void> {
  const agents = Array.from(agentCache.values());
  agentCache.clear();
  await Promise.all(agents.map((a) => a.close()));
}
