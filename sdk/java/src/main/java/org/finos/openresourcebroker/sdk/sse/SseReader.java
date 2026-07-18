// Layer 5: SSE Reader + Reconnect
package org.finos.openresourcebroker.sdk.sse;

import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * Parses SSE (Server-Sent Events) frames from an InputStream.
 *
 * <p>SSE wire format used by ORB:
 * <pre>
 *   data: &lt;json&gt;\n\n          — normal event
 *   data: {}\n\n              — terminal sentinel (stream done)
 * </pre>
 *
 * <p>Only {@code data:} lines are processed; {@code event:}, {@code id:},
 * and {@code retry:} are accepted but ignored (ORB does not use them).
 *
 * <p>The frame buffer is bounded at {@link #MAX_SSE_FRAME_BYTES} (4 MiB, matching
 * the Go/TS/Kotlin/C# SDKs).  A server that never emits a newline or emits an
 * enormous {@code data:} line would otherwise grow client memory without bound;
 * on overflow the reader throws a terminal {@link IOException} so the caller can
 * abort rather than reconnect and spin on the same oversized frame.
 */
public class SseReader implements Closeable {

    /** Maximum size of a single accumulated SSE frame (4 MiB). */
    public static final int MAX_SSE_FRAME_BYTES = 4 * 1024 * 1024;

    private final InputStream stream;

    public SseReader(InputStream stream) {
        this.stream = stream;
    }

    /**
     * Read the next complete SSE frame.
     *
     * @return the next frame, or {@code null} when the stream ends normally
     * @throws IOException if an I/O error occurs or a frame exceeds
     *                     {@link #MAX_SSE_FRAME_BYTES}
     */
    public SseFrame next() throws IOException {
        List<String> dataLines = new ArrayList<>();
        long frameBytes = 0;

        String line;
        while ((line = readLine()) != null) {
            if (line.isEmpty()) {
                // Blank line = end of event
                if (dataLines.isEmpty()) {
                    continue; // heartbeat / keep-alive blank line
                }
                String data = String.join("\n", dataLines);
                dataLines.clear();
                return new SseFrame(data);
            }

            if (line.startsWith("data:")) {
                String val = line.substring(5);
                if (!val.isEmpty() && val.charAt(0) == ' ') {
                    val = val.substring(1);
                }
                frameBytes += val.length() + 1; // +1 for the joining newline
                if (frameBytes > MAX_SSE_FRAME_BYTES) {
                    throw new IOException("SSE frame exceeds maximum allowed "
                            + MAX_SSE_FRAME_BYTES + " bytes");
                }
                dataLines.add(val);
            }
            // Ignore event:, id:, retry: lines
        }

        // EOF
        return null;
    }

    /**
     * Read a single line, bounded by {@link #MAX_SSE_FRAME_BYTES}, terminated by
     * LF (CR is stripped).  Reads raw bytes so an unbounded, newline-free line
     * cannot exhaust the heap via a decoding buffer.
     *
     * @return the line without its terminator, or {@code null} at EOF before any
     *         byte was read
     */
    private String readLine() throws IOException {
        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream(256);
        int b;
        boolean any = false;
        while ((b = stream.read()) != -1) {
            any = true;
            if (b == '\n') {
                return decode(buf);
            }
            if (b == '\r') {
                continue; // strip CR; the following LF ends the line
            }
            if (buf.size() >= MAX_SSE_FRAME_BYTES) {
                throw new IOException("SSE line exceeds maximum allowed "
                        + MAX_SSE_FRAME_BYTES + " bytes");
            }
            buf.write(b);
        }
        if (!any && buf.size() == 0) return null;
        return decode(buf);
    }

    private static String decode(java.io.ByteArrayOutputStream buf) {
        return buf.toString(StandardCharsets.UTF_8);
    }

    @Override
    public void close() {
        if (stream == null) return;
        try {
            stream.close();
        } catch (IOException ignored) {}
    }
}
