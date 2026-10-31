package org.finos.openresourcebroker.sdk.unit

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.finos.openresourcebroker.sdk.sse.*
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Assertions.assertNull
import java.io.ByteArrayInputStream

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class SseReaderTest {

    private fun streamOf(text: String) = ByteArrayInputStream(text.toByteArray(Charsets.UTF_8))

    @Test
    fun `parse single data event`() = runTest {
        val sse = "data: {\"hello\":\"world\"}\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(1, frames.size)
        assertEquals("""{"hello":"world"}""", frames[0].data)
        assertNull(frames[0].event)
        assertNull(frames[0].id)
    }

    @Test
    fun `parse event with id and event type`() = runTest {
        val sse = "id: abc123\nevent: status\ndata: payload\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(1, frames.size)
        assertEquals("abc123", frames[0].id)
        assertEquals("status", frames[0].event)
        assertEquals("payload", frames[0].data)
    }

    @Test
    fun `parse retry directive`() = runTest {
        val sse = "retry: 5000\ndata: x\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(1, frames.size)
        assertEquals(5000L, frames[0].retry)
    }

    @Test
    fun `parse multiple events`() = runTest {
        val sse = "data: first\n\ndata: second\n\ndata: third\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(3, frames.size)
        assertEquals("first", frames[0].data)
        assertEquals("second", frames[1].data)
        assertEquals("third", frames[2].data)
    }

    @Test
    fun `sentinel detection`() {
        val sentinel = SseFrame(data = "{}")
        assertTrue(sentinel.isSentinel())

        val normal = SseFrame(data = """{"status":"running"}""")
        assertFalse(normal.isSentinel())
    }

    @Test
    fun `comments are ignored`() = runTest {
        val sse = ": this is a comment\ndata: actual\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(1, frames.size)
        assertEquals("actual", frames[0].data)
    }

    @Test
    fun `multi-line data is joined`() = runTest {
        val sse = "data: line1\ndata: line2\n\n"
        val frames = parseSseFrames(streamOf(sse)).toList()
        assertEquals(1, frames.size)
        assertEquals("line1\nline2", frames[0].data)
    }

    @Test
    fun `empty stream produces no frames`() = runTest {
        val frames = parseSseFrames(streamOf("")).toList()
        assertEquals(0, frames.size)
    }

    @Test
    fun `oversized data line throws rather than growing unbounded`() = runTest {
        // A single data line larger than the 4 MiB cap must terminate the parse
        // with an IOException instead of accumulating without bound.
        val huge = "x".repeat(MAX_SSE_FRAME_BYTES + 1024)
        val sse = "data: $huge\n\n"
        assertThrows<java.io.IOException> {
            parseSseFrames(streamOf(sse)).toList()
        }
    }

    @Test
    fun `never-terminated line is bounded`() = runTest {
        // A stream that never emits a newline must not grow the heap without bound.
        val huge = "y".repeat(MAX_SSE_FRAME_BYTES + 1024)
        assertThrows<java.io.IOException> {
            parseSseFrames(streamOf(huge)).toList()
        }
    }

    @Test
    fun `sseStream terminates on sentinel`() = runTest {
        // Provide a connect function that returns a sentinel after one event
        val sse = "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"running\"}]}\n\ndata: {}\n\n"
        var called = 0
        val flow = sseStream(connect = { _ ->
            called++
            streamOf(sse)
        })
        val frames = flow.toList()
        // Should get 2 frames (the event + sentinel), then stop
        // sentinel causes termination so reconnect doesn't happen
        assertEquals(1, called, "connect() should be called exactly once")
    }

    private val inProgress =
        "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"in_progress\"}]}\n\n"

    private suspend fun collectStatusStream(wire: String): Pair<List<SseFrame>, Int> {
        var called = 0
        val frames = sseStream(
            connect = { _ ->
                called++
                streamOf(wire)
            },
            failOnOutcomeFrames = true,
        ).toList()
        return frames to called
    }

    @Test
    fun `timeout frame raises a distinct timeout error and does not reconnect`() = runTest {
        var called = 0
        val wire = inProgress + "event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n"
        val e = assertThrows<org.finos.openresourcebroker.sdk.client.OrbStreamTimeoutError> {
            sseStream(
                connect = { _ -> called++; streamOf(wire) },
                failOnOutcomeFrames = true,
            ).toList()
        }
        assertTrue(e is org.finos.openresourcebroker.sdk.client.OrbError)
        assertEquals(1, called)
    }

    @Test
    fun `error frame raises an error and does not reconnect`() = runTest {
        var called = 0
        val wire = inProgress + "event: error\ndata: {\"error\": \"internal\"}\n\ndata: {}\n\n"
        assertThrows<org.finos.openresourcebroker.sdk.client.OrbStreamError> {
            sseStream(
                connect = { _ -> called++; streamOf(wire) },
                failOnOutcomeFrames = true,
            ).toList()
        }
        assertEquals(1, called)
    }

    @Test
    fun `complete frame is not an error`() = runTest {
        val wire = inProgress + "event: complete\ndata: {\"status\": \"failed\"}\n\ndata: {}\n\n"
        val (frames, called) = collectStatusStream(wire)
        assertEquals(1, called)
        assertEquals(listOf(null, "complete", null), frames.map { it.event })
    }

    @Test
    fun `final status frame survives complete outcome and sentinel`() = runTest {
        val wire = inProgress +
            "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"complete\"}]}\n\n" +
            "event: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n"
        val (frames, _) = collectStatusStream(wire)
        val lastStatusFrame = frames.last { it.data.contains("\"requests\"") }
        assertTrue(lastStatusFrame.data.contains("\"complete\""))
    }

    @Test
    fun `servers without named frames behave as before`() = runTest {
        val wire = inProgress + "data: {}\n\n"
        val (frames, called) = collectStatusStream(wire)
        assertEquals(1, called)
        assertEquals(2, frames.size)
        assertTrue(frames.last().isSentinel())
    }

    @Test
    fun `outcome frames are passed through when not requested`() = runTest {
        val wire = "event: error\ndata: {\"error\": \"internal\"}\n\ndata: {}\n\n"
        val frames = sseStream(connect = { _ -> streamOf(wire) }).toList()
        assertEquals("error", frames.first().event)
    }

    // Output of the server's default scheduler formatter for a request status
    // stream frame: numeric launch_time, *_ip_address keys, a null launch_time.
    private val golden = """{"requests":[{"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","status":"complete","template_id":"t1","requested_count":2,"created_at":"2026-10-31T01:00:00Z","machine_ids":[],"message":"done","metadata":{},"request_type":"acquire","desired_capacity":1,"successful_count":2,"failed_count":0,"error_details":{},"provider_data":{},"version":0,"resource_ids":[],"machines":[{"machine_id":"i-0abc123def4567890","name":"ip-10-0-1-12","result":"succeed","status":"running","private_ip_address":"10.0.1.12","public_ip_address":"54.12.34.56","instance_type":"t3.micro","price_type":"ondemand","cloud_host_id":"ip-10-0-1-12.ec2.internal","launch_time":1793411323,"request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":""},{"machine_id":"i-0def456abc7890123","name":"ip-10-0-1-13","result":"executing","status":"pending","request_id":"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73","message":"","launch_time":null,"cloud_host_id":null}]}],"message":"Request status retrieved successfully","count":1}"""

    @Test
    fun `golden server payload parses status launch time and IPs`() {
        val payload = com.google.gson.Gson().fromJson(golden, OrbSsePayload::class.java)
        val req = payload.requests!!.first()
        assertEquals("complete", req.status)
        assertEquals(2, req.machines!!.size)
        val m = req.machines!![0]
        assertEquals("10.0.1.12", m.private_ip_address)
        assertEquals("54.12.34.56", m.public_ip_address)
        assertEquals("1793411323", m.launch_time)
        assertNull(req.machines!![1].launch_time)
    }
}
