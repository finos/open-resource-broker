/**
 * Layer 4: Authentication
 *
 * Supports:
 *   - None (no-op)
 *   - Bearer token (static string or dynamic function)
 *   - AWS SigV4 using @aws-sdk/signature-v4 (NOT hand-rolled)
 *
 * Auth is applied as an Axios request interceptor so it runs for EVERY request
 * including SSE requests.
 */

import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import { SignatureV4 } from "@smithy/signature-v4";
import type { AwsCredentialIdentity, Provider } from "@smithy/types";
import { HttpRequest as SmithyHttpRequest } from "@smithy/protocol-http";
import { Sha256 } from "@aws-crypto/sha256-js";

// ---------------------------------------------------------------------------
// Auth option types
// ---------------------------------------------------------------------------

/**
 * Open extension point: implement AuthProvider to add custom auth strategies
 * (e.g. Azure Workload Identity, GCP service-account tokens, OIDC exchange).
 *
 * Example — Azure Managed Identity:
 *
 *   const azureAuth: AuthProvider = {
 *     async apply(config) {
 *       const token = await getAzureToken();
 *       config.headers.set("Authorization", `Bearer ${token}`);
 *     },
 *   };
 *   const client = new OrbClient({ auth: { type: "custom", provider: azureAuth } });
 */
export interface AuthProvider {
  /**
   * Mutate the Axios request config to inject authentication headers.
   * Called before every request (including SSE requests).
   */
  apply(config: InternalAxiosRequestConfig): Promise<void>;
}

export type AuthOption =
  | { type: "none" }
  | { type: "bearer"; token: string | (() => string | Promise<string>) }
  | {
      type: "sigv4";
      region: string;
      service?: string;
      credentials:
        | AwsCredentialIdentity
        | Provider<AwsCredentialIdentity>;
    }
  | {
      /** Escape hatch for custom auth providers (Azure, GCP, OIDC, etc.). */
      type: "custom";
      provider: AuthProvider;
    };

// ---------------------------------------------------------------------------
// Attach auth to an Axios instance as an interceptor
// ---------------------------------------------------------------------------

export function attachAuth(instance: AxiosInstance, auth: AuthOption): void {
  if (auth.type === "none") return;

  if (auth.type === "bearer") {
    instance.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
      const token =
        typeof auth.token === "function" ? await auth.token() : auth.token;
      config.headers.set("Authorization", `Bearer ${token}`);
      return config;
    });
    return;
  }

  if (auth.type === "custom") {
    instance.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
      await auth.provider.apply(config);
      return config;
    });
    return;
  }

  if (auth.type === "sigv4") {
    const signer = new SignatureV4({
      credentials: auth.credentials,
      region: auth.region,
      service: auth.service ?? "execute-api",
      sha256: Sha256,
    });

    instance.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
      // Build the full URL
      const baseURL = config.baseURL ?? "";
      const url = config.url?.startsWith("http")
        ? config.url
        : `${baseURL}${config.url ?? ""}`;

      const parsed = new URL(url);

      // Collect headers that will be signed
      const headers: Record<string, string> = {
        host: parsed.host,
      };
      if (config.headers) {
        for (const [k, v] of Object.entries(config.headers)) {
          if (
            v !== undefined &&
            v !== null &&
            typeof v !== "object" &&
            k.toLowerCase() !== "host"
          ) {
            headers[k.toLowerCase()] = String(v);
          }
        }
      }

      // Serialize body for signing
      let body: string | undefined;
      if (config.data !== undefined && config.data !== null) {
        body =
          typeof config.data === "string"
            ? config.data
            : JSON.stringify(config.data);
      }

      const smithyRequest = new SmithyHttpRequest({
        method: (config.method ?? "GET").toUpperCase(),
        protocol: parsed.protocol,
        hostname: parsed.hostname,
        port: parsed.port ? Number(parsed.port) : undefined,
        path: parsed.pathname + parsed.search,
        headers,
        body,
      });

      const signed = await signer.sign(smithyRequest);

      // Apply signed headers to the Axios config
      for (const [k, v] of Object.entries(signed.headers)) {
        config.headers.set(k, v);
      }

      return config;
    });
  }
}

// ---------------------------------------------------------------------------
// Helper: build an auth option from environment variables (AWS standard chain)
// ---------------------------------------------------------------------------

/**
 * Build a SigV4 auth option that reads credentials from the AWS standard chain
 * (env vars → ~/.aws/credentials → instance metadata).
 */
export async function sigV4FromEnv(
  region?: string
): Promise<Extract<AuthOption, { type: "sigv4" }>> {
  // Use @aws-sdk/credential-providers for the full chain
  const { fromNodeProviderChain } = await import("@aws-sdk/credential-providers");
  return {
    type: "sigv4",
    region: region ?? process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION ?? "us-east-1",
    credentials: fromNodeProviderChain(),
  };
}
