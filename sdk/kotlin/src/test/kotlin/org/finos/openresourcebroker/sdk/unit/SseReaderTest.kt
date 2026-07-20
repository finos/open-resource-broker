package org.finos.openresourcebroker.sdk.unit

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.finos.openresourcebroker.sdk.sse.*
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
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
}
