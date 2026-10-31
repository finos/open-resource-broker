package org.finos.openresourcebroker.sdk.unit;

import org.finos.openresourcebroker.sdk.client.OrbError;
import org.finos.openresourcebroker.sdk.client.OrbStreamException;
import org.finos.openresourcebroker.sdk.client.OrbStreamTimeoutException;
import org.finos.openresourcebroker.sdk.client.StreamOutcome;
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
    void namedOutcomeFramesCarryNoRequestsAndSentinelIsLast() throws Exception {
        String[] outcomes = {
            "event: complete\ndata: {\"status\": \"failed\"}\n\n",
            "event: error\ndata: {\"error\": \"internal\"}\n\n",
            "event: timeout\ndata: {\"status\": \"timeout\"}\n\n",
        };
        for (String outcome : outcomes) {
            String wire = "data: {\"requests\":[{\"status\":\"in_progress\"}]}\n\n"
                    + outcome + "data: {}\n\n";
            try (SseReader r = new SseReader(bytes(wire))) {
                SseFrame status = r.next();
                assertNotNull(status);
                assertTrue(status.data().contains("\"requests\""));
                SseFrame named = r.next();
                assertNotNull(named);
                assertFalse(named.isSentinel());
                assertFalse(named.data().contains("\"requests\""));
                SseFrame last = r.next();
                assertNotNull(last);
                assertTrue(last.isSentinel());
                assertNull(r.next());
            }
        }
    }

    @Test
    void readerExposesEventNameAndDoesNotLeakItToNextFrame() throws Exception {
        String wire = "event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n";
        try (SseReader r = new SseReader(bytes(wire))) {
            assertEquals("timeout", r.next().event());
            SseFrame next = r.next();
            assertNull(next.event());
            assertTrue(next.isSentinel());
        }
    }

    @Test
    void timeoutFrameRaisesDistinctTimeoutError() {
        SseFrame frame = new SseFrame("{\"status\": \"timeout\"}", "timeout");
        OrbError e = assertThrows(OrbStreamTimeoutException.class,
                () -> StreamOutcome.throwIfFailed(frame));
        assertFalse(e instanceof OrbStreamException);
    }

    @Test
    void errorFrameRaisesError() {
        SseFrame frame = new SseFrame("{\"error\": \"internal\"}", "error");
        OrbError e = assertThrows(OrbStreamException.class,
                () -> StreamOutcome.throwIfFailed(frame));
        assertFalse(e instanceof OrbStreamTimeoutException);
    }

    @Test
    void finalStatusFrameSurvivesCompleteOutcomeAndSentinel() throws Exception {
        String wire = "data: {\"requests\":[{\"status\":\"in_progress\"}]}\n\n"
                + "data: {\"requests\":[{\"status\":\"complete\"}]}\n\n"
                + "event: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n";
        String lastStatusFrame = null;
        try (SseReader r = new SseReader(bytes(wire))) {
            SseFrame f;
            while ((f = r.next()) != null) {
                StreamOutcome.throwIfFailed(f);
                if (f.data().contains("\"requests\"")) lastStatusFrame = f.data();
            }
        }
        assertNotNull(lastStatusFrame);
        assertTrue(lastStatusFrame.contains("\"complete\""));
    }

    // Output of the server's default scheduler formatter for a request status
    // stream frame: numeric launch_time, *_ip_address keys, a null launch_time.
    private static final String GOLDEN = "{\"requests\":[{\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"status\":\"complete\",\"template_id\":\"t1\",\"requested_count\":2,\"created_at\":\"2026-10-31T01:00:00Z\",\"machine_ids\":[],\"message\":\"done\",\"metadata\":{},\"request_type\":\"acquire\",\"desired_capacity\":1,\"successful_count\":2,\"failed_count\":0,\"error_details\":{},\"provider_data\":{},\"version\":0,\"resource_ids\":[],\"machines\":[{\"machine_id\":\"i-0abc123def4567890\",\"name\":\"ip-10-0-1-12\",\"result\":\"succeed\",\"status\":\"running\",\"private_ip_address\":\"10.0.1.12\",\"public_ip_address\":\"54.12.34.56\",\"instance_type\":\"t3.micro\",\"price_type\":\"ondemand\",\"cloud_host_id\":\"ip-10-0-1-12.ec2.internal\",\"launch_time\":1793411323,\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\"},{\"machine_id\":\"i-0def456abc7890123\",\"name\":\"ip-10-0-1-13\",\"result\":\"executing\",\"status\":\"pending\",\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\",\"launch_time\":null,\"cloud_host_id\":null}]}],\"message\":\"Request status retrieved successfully\",\"count\":1}";

    @Test
    void goldenServerPayloadParsesStatusLaunchTimeAndIps() throws Exception {
        org.finos.openresourcebroker.sdk.sse.OrbSsePayload payload =
                new com.fasterxml.jackson.databind.ObjectMapper()
                        .readValue(GOLDEN, org.finos.openresourcebroker.sdk.sse.OrbSsePayload.class);
        var req = payload.firstRequest();
        assertNotNull(req);
        assertEquals("complete", req.getStatus());
        assertEquals(2, req.getMachines().size());
        var m = req.getMachines().get(0);
        assertEquals("10.0.1.12", m.getPrivateIp());
        assertEquals("54.12.34.56", m.getPublicIp());
        assertEquals("1793411323", m.getLaunchTime());
        assertNull(req.getMachines().get(1).getLaunchTime());
    }

    @Test
    void completeAndPlainFramesAreNotErrors() {
        assertDoesNotThrow(() -> StreamOutcome.throwIfFailed(
                new SseFrame("{\"status\": \"failed\"}", "complete")));
        assertDoesNotThrow(() -> StreamOutcome.throwIfFailed(
                new SseFrame("{\"requests\":[]}")));
        assertDoesNotThrow(() -> StreamOutcome.throwIfFailed(new SseFrame("{}")));
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
