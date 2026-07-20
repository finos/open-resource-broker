package org.finos.openresourcebroker.sdk.unit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Static operation-completeness check (Java leg).
 *
 * <p>Ports the intent of the Go SDK's {@code conformance_test.go} to Java as a
 * pure unit check (no ORB process needed): it enumerates every operationId
 * declared in {@code sdk/spec/openapi.json} and asserts the hand-written client
 * ({@code OrbClient.java}) covers each one. Every client method documents its
 * operationId in a Javadoc comment (the same convention
 * {@code validate_sdk_spec_conformance.py} relies on), so a missing operation —
 * e.g. a brand-new endpoint added to the spec — leaves its operationId absent
 * from the client source and fails this test.
 *
 * <p>The net effect is the cross-language guarantee: a new spec endpoint now
 * fails CI in every language's completeness test, not only Go's. This test is
 * deliberately un-tagged so it runs in the default {@code test} task without a
 * live orb.
 */
class OperationCompletenessTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final Set<String> HTTP_METHODS =
            Set.of("get", "post", "put", "delete", "patch", "head");

    // The Gradle build sets the working dir to sdk/java, so the spec lives at
    // ../spec/openapi.json and the client at src/main/java/.../OrbClient.java.
    private static final Path SPEC_PATH = Paths.get("..", "spec", "openapi.json");
    private static final Path CLIENT_PATH = Paths.get(
            "src", "main", "java", "org", "finos", "openresourcebroker",
            "sdk", "client", "OrbClient.java");

    private static List<String> specOperationIds() throws Exception {
        assertTrue(Files.exists(SPEC_PATH),
                "spec not found: " + SPEC_PATH.toAbsolutePath());
        JsonNode doc = MAPPER.readTree(SPEC_PATH.toFile());
        JsonNode paths = doc.get("paths");
        List<String> ids = new ArrayList<>();
        for (Iterator<Map.Entry<String, JsonNode>> it = paths.fields(); it.hasNext(); ) {
            JsonNode methods = it.next().getValue();
            for (Iterator<Map.Entry<String, JsonNode>> mIt = methods.fields(); mIt.hasNext(); ) {
                Map.Entry<String, JsonNode> m = mIt.next();
                if (HTTP_METHODS.contains(m.getKey().toLowerCase())) {
                    JsonNode opId = m.getValue().get("operationId");
                    if (opId != null && !opId.asText().isEmpty()) {
                        ids.add(opId.asText());
                    }
                }
            }
        }
        return ids;
    }

    @Test
    void specDeclaresExactly45Operations() throws Exception {
        // Mirrors Go's `if len(ops) != 45` sentinel: a spec that grows or shrinks
        // forces a deliberate update rather than silently under-covering.
        assertEquals(45, specOperationIds().size(),
                "spec operation count changed — update coverage");
    }

    @Test
    void clientCoversEverySpecOperation() throws Exception {
        assertTrue(Files.exists(CLIENT_PATH),
                "client not found: " + CLIENT_PATH.toAbsolutePath());
        String clientSource = Files.readString(CLIENT_PATH);
        List<String> missing = new ArrayList<>();
        for (String opId : specOperationIds()) {
            // Match each operationId as a WHOLE WORD, not a plain substring.  A
            // plain contains() is vacuous when one operationId is a prefix of
            // another — "getRequest" is a substring of "getRequestStatus"/
            // "getRequestTimeline" (and "getMachine" of "getMachineMetrics"), so
            // the check would still pass even if the getRequest method were
            // deleted entirely.  operationIds are [A-Za-z]+ tokens and the Javadoc
            // convention is "<id> — VERB /path", so a \b word boundary requires
            // the id to appear as its own token (a non-identifier char must
            // follow) and is NOT satisfied by a longer id that merely starts
            // with it.
            Pattern token = Pattern.compile("\\b" + Pattern.quote(opId) + "\\b");
            if (!token.matcher(clientSource).find()) {
                missing.add(opId);
            }
        }
        assertTrue(missing.isEmpty(),
                "OrbClient.java does not cover " + missing.size()
                        + " spec operation(s): " + missing);
    }
}
