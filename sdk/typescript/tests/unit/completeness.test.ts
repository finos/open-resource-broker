/**
 * Static operation-completeness check (TypeScript leg).
 *
 * Ports the intent of the Go SDK's conformance_test.go to TypeScript as a pure
 * static/unit check (no orb needed): it enumerates every operationId declared in
 * sdk/spec/openapi.json and asserts the hand-written client (src/client.ts)
 * covers each one. Every client method documents its operationId in a doc
 * comment (the same convention validate_sdk_spec_conformance.py relies on), so a
 * missing operation — e.g. a brand-new endpoint added to the spec — leaves its
 * operationId absent from the client source and fails this test.
 *
 * The net effect is the cross-language guarantee: a new spec endpoint now fails
 * CI in every language's completeness test, not only Go's.
 */
import * as fs from "fs";
import * as path from "path";

// __dirname is sdk/typescript/tests/unit; three "../" lands at sdk/ (same base
// the parity test uses to reach sdk/parity), so spec/ is sibling of typescript/.
const SPEC_PATH = path.resolve(__dirname, "..", "..", "..", "spec", "openapi.json");
const CLIENT_PATH = path.resolve(__dirname, "..", "..", "src", "client.ts");

const HTTP_METHODS = new Set(["get", "post", "put", "delete", "patch", "head"]);

function specOperationIds(): string[] {
  const spec = JSON.parse(fs.readFileSync(SPEC_PATH, "utf8")) as {
    paths: Record<string, Record<string, { operationId?: string }>>;
  };
  const ids: string[] = [];
  for (const methods of Object.values(spec.paths)) {
    for (const [method, def] of Object.entries(methods)) {
      if (HTTP_METHODS.has(method.toLowerCase()) && def.operationId) {
        ids.push(def.operationId);
      }
    }
  }
  return ids;
}

describe("SDK operation completeness vs OpenAPI spec", () => {
  const operationIds = specOperationIds();
  const clientSource = fs.readFileSync(CLIENT_PATH, "utf8");

  it("declares exactly the operations the spec defines (guards against drift)", () => {
    // Mirrors Go's `if len(ops) != 44` sentinel: if the spec grows or shrinks,
    // this forces a deliberate update rather than silently under-covering.
    expect(operationIds.length).toBe(45);
  });

  it("covers every spec operationId in the hand-written client", () => {
    const missing = operationIds.filter((id) => !clientSource.includes(id));
    expect(missing).toEqual([]);
  });
});
