/**
 * Regression guard for finos/open-resource-broker#457 (TS SDK type-check
 * failure).
 *
 * The generated SDK ships generated/common.ts (gitignored, produced on demand
 * by `make sdk-typescript-generate`). TypeScript 6.0 strict reports TS2527 on
 * the generated createRequestFunction — its inferred type references an
 * inaccessible unique symbol — unless the post-generation patch in
 * scripts/patch-generated-common.mjs is applied. This test pins the patched
 * shape so a generator bump that silently drops the fix fails CI here, and a
 * generator bump that changes the expected strings fails the patch script
 * itself with a clear message.
 *
 * generated/ is populated before tests in every CI path (sdk.yml runs
 * `make sdk-typescript-check-drift` → sdk-typescript-generate first), so the
 * file is always present when this test runs in CI.
 */
import * as fs from "fs";
import * as path from "path";

const GENERATED_COMMON = path.resolve(
  __dirname,
  "..",
  "..",
  "generated",
  "common.ts",
);

describe("generated/common.ts createRequestFunction type-check guard", () => {
  const source = fs.readFileSync(GENERATED_COMMON, "utf8");

  it("carries the explicit public signature (TS2527 annotation)", () => {
    // The annotated signature must survive generation so `tsc --noEmit`
    // (npm run typecheck) stays green under TS 6.0 strict.
    expect(source).toContain(
      "configuration?: Configuration): <T = unknown, R = AxiosResponse<T>>(axios: AxiosInstance, basePath: string) => Promise<R> {",
    );
  });

  it("bridges the axios request promise so the signature type-checks", () => {
    expect(source).toContain("return axios.request<T, R>(axiosRequestArgs) as Promise<R>;");
  });

  it("does not regress to the unannotated generator default", () => {
    // If the patch is dropped (generator bump / sed removal), the unannotated
    // form comes back and typecheck fails again — detect it statically here.
    expect(
      source.includes(
        "configuration?: Configuration) {\n    return <T = unknown, R = AxiosResponse<T>>(axios: AxiosInstance = globalAxios, basePath: string = BASE_PATH) => {",
      ),
    ).toBe(false);
  });
});
