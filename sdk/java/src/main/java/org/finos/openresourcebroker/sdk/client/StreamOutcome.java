package org.finos.openresourcebroker.sdk.client;

import org.finos.openresourcebroker.sdk.sse.SseFrame;

/**
 * Interprets the named outcome frames of a request status stream. {@code
 * timeout} and {@code error} end the stream without the request being finished;
 * {@code complete} frames, plain data frames and streams from servers that
 * predate named frames are not errors.
 */
public final class StreamOutcome {

    private StreamOutcome() {}

    public static void throwIfFailed(SseFrame frame) {
        String event = frame.event();
        if ("timeout".equals(event)) throw new OrbStreamTimeoutException();
        if ("error".equals(event)) throw new OrbStreamException();
    }
}
