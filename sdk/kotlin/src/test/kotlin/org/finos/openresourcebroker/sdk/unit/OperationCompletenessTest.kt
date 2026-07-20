package org.finos.openresourcebroker.sdk.unit

import com.google.gson.Gson
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Paths

/**
 * Static operation-completeness check (Kotlin leg).
 *
 * Ports the intent of the Go SDK's conformance_test.go to Kotlin as a pure unit
 * check (no ORB process needed): it enumerates every operationId declared in
 * sdk/spec/openapi.json and asserts the hand-written client (OrbClient.kt)
 * covers each one. Every client method documents its operationId in a KDoc
 * comment (the same convention validate_sdk_spec_conformance.py relies on), so a
 * missing operation — e.g. a brand-new endpoint added to the spec — leaves its
 * operationId absent from the client source and fails this test.
 *
 * The net effect is the cross-language guarantee: a new spec endpoint now fails
 * CI in every language's completeness test, not only Go's. This test lives in
 * the unit package so it runs in the default `test` task without a live orb.
 */
class OperationCompletenessTest {

    private val httpMethods = setOf("get", "post", "put", "delete", "patch", "head")

    // The Gradle build runs from sdk/kotlin, so the spec is at ../spec/openapi.json
    // and the client at src/main/kotlin/.../OrbClient.kt.
    private val specPath = Paths.get("..", "spec", "openapi.json")
    private val clientPath = Paths.get(
        "src", "main", "kotlin", "org", "finos", "openresourcebroker",
        "sdk", "client", "OrbClient.kt"
    )

    @Suppress("UNCHECKED_CAST")
    private fun specOperationIds(): List<String> {
        assertTrue(Files.exists(specPath), "spec not found: ${specPath.toAbsolutePath()}")
        val doc = Gson().fromJson(Files.readString(specPath), Map::class.java)
        val paths = doc["paths"] as Map<String, Map<String, Any?>>
        val ids = mutableListOf<String>()
        for (methods in paths.values) {
            for ((method, definition) in methods) {
                if (method.lowercase() in httpMethods && definition is Map<*, *>) {
                    val opId = definition["operationId"] as? String
                    if (!opId.isNullOrEmpty()) ids.add(opId)
                }
            }
        }
        return ids
    }

    @Test
    fun specDeclaresExactly45Operations() {
        // Mirrors Go's `if len(ops) != 45` sentinel: a spec that grows or shrinks
        // forces a deliberate update rather than silently under-covering.
        assertEquals(45, specOperationIds().size, "spec operation count changed — update coverage")
    }

    @Test
    fun clientCoversEverySpecOperation() {
        assertTrue(Files.exists(clientPath), "client not found: ${clientPath.toAbsolutePath()}")
        val clientSource = Files.readString(clientPath)
        // Match each operationId as a WHOLE WORD, not a plain substring.  A plain
        // contains() is vacuous when one operationId is a prefix of another —
        // "getRequest" is a substring of "getRequestStatus"/"getRequestTimeline"
        // (and "getMachine" of "getMachineMetrics"), so the check would still pass
        // even if the getRequest method were deleted entirely.  operationIds are
        // [A-Za-z]+ tokens and the KDoc convention is "<id> — VERB /path", so a \b
        // word boundary requires the id to appear as its own token (a
        // non-identifier char must follow) and is NOT satisfied by a longer id
        // that merely starts with it.
        val missing = specOperationIds().filterNot { id ->
            Regex("\\b" + Regex.escape(id) + "\\b").containsMatchIn(clientSource)
        }
        assertTrue(
            missing.isEmpty(),
            "OrbClient.kt does not cover ${missing.size} spec operation(s): $missing"
        )
    }
}
