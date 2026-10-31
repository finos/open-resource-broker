package org.finos.openresourcebroker.sdk.sse;

/**
 * A single parsed SSE frame: the {@code data:} payload plus the optional
 * {@code event:} name ({@code null} when the frame has none).
 *
 * <p>The request status stream names its outcome frames {@code complete},
 * {@code error} and {@code timeout}; servers that predate them send plain data
 * frames only.
 */
public record SseFrame(String data, String event) {

    /** A frame without an event name. */
    public SseFrame(String data) {
        this(data, null);
    }

    /**
     * Returns true if this frame is the ORB terminal sentinel ({@code data: {}}).
     * ORB signals end-of-stream with either {@code {}} or {@code {"sentinel":true}}.
     */
    public boolean isSentinel() {
        if (data == null) return false;
        String trimmed = data.trim();
        return trimmed.equals("{}") || trimmed.contains("\"sentinel\"");
    }
}
