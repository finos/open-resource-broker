package org.finos.openresourcebroker.sdk.unit;

import org.finos.openresourcebroker.sdk.sse.SseFrame;
import org.finos.openresourcebroker.sdk.sse.SseReader;

import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for {@link SseReader}, including the 4 MiB frame-buffer bound that
 * mirrors the Go/TS/Kotlin/C# SDKs.
 */
class SseReaderTest {

    @Test
    void parsesDataFrame() throws Exception {
        String wire = "data: {\"requests\":[]}\n\n";
        try (SseReader r = new SseReader(bytes(wire))) {
            SseFrame frame = r.next();
            assertNotNull(frame);
            assertEquals("{\"requests\":[]}", frame.data());
            assertNull(r.next(), "stream should end after the single frame");
        }
    }

    @Test
    void skipsHeartbeatBlankLines() throws Exception {
        String wire = "\n\ndata: {}\n\n";
        try (SseReader r = new SseReader(bytes(wire))) {
            SseFrame frame = r.next();
            assertNotNull(frame);
            assertTrue(frame.isSentinel());
        }
    }

    @Test
    void boundsOversizedFrame() {
        // A single data line larger than the 4 MiB cap must terminate with an
        // IOException rather than growing the heap without bound.
        int over = SseReader.MAX_SSE_FRAME_BYTES + 1024;
        StringBuilder sb = new StringBuilder("data: ");
        for (int i = 0; i < over; i++) sb.append('a');
        // Note: no trailing newline — simulates a never-terminated giant line.
        InputStream in = bytes(sb.toString());
        try (SseReader r = new SseReader(in)) {
            assertThrows(IOException.class, r::next,
                    "an oversized SSE frame must throw, not exhaust memory");
        } catch (Exception e) {
            fail("close() should not throw: " + e);
        }
    }

    private static InputStream bytes(String s) {
        return new ByteArrayInputStream(s.getBytes(StandardCharsets.UTF_8));
    }
}
