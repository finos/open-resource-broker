#!/usr/bin/env node
/**
 * Post-generation patch for sdk/typescript/generated/common.ts.
 *
 * TypeScript 6.0 strict reports TS2527 on the generated createRequestFunction:
 *   "The inferred type of 'createRequestFunction' references an inaccessible
 *    'unique symbol' type. A type annotation is necessary."
 * (finos/open-resource-broker#457)
 *
 * The openapi-generator template cannot emit a correct annotation because the
 * true axios return type (AxiosResponseResult) is not exported from axios's
 * typings, so the inferred type would keep leaking the inaccessible symbol.
 * This script gives the request factory an explicit public signature and
 * bridges the axios request promise, which keeps `tsc --noEmit` green without
 * weakening strictness.
 *
 * Invoked by `make sdk-typescript-generate` (makefiles/sdk.mk) right after the
 * generator run and the index.ts api-strip. Keep the expected strings in sync
 * with openapi-generator v7.23.0 (pinned in makefiles/sdk.mk).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const commonTs = path.resolve(__dirname, "..", "generated", "common.ts");

const OLD_DECL =
  "export const createRequestFunction = function (axiosArgs: RequestArgs, globalAxios: AxiosInstance, BASE_PATH: string, configuration?: Configuration) {";
const NEW_DECL =
  "export const createRequestFunction = function (axiosArgs: RequestArgs, globalAxios: AxiosInstance, BASE_PATH: string, configuration?: Configuration): <T = unknown, R = AxiosResponse<T>>(axios: AxiosInstance, basePath: string) => Promise<R> {";

const OLD_RETURN = "return axios.request<T, R>(axiosRequestArgs);";
const NEW_RETURN = "return axios.request<T, R>(axiosRequestArgs) as Promise<R>;";

const src = fs.readFileSync(commonTs, "utf8");
if (!src.includes(OLD_DECL) || !src.includes(OLD_RETURN)) {
  console.error(
    "patch-generated-common: expected generator output not found in generated/common.ts; " +
      "generator version mismatch? (see makefiles/sdk.mk OPENAPI_GENERATOR_VERSION)",
  );
  process.exit(1);
}

const patched = src.replace(OLD_DECL, NEW_DECL).replace(OLD_RETURN, NEW_RETURN);
fs.writeFileSync(commonTs, patched);
console.log("patch-generated-common: applied TS2527 signature to generated/common.ts");
