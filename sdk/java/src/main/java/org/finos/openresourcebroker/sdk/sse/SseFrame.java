package org.finos.openresourcebroker.sdk.sse;

/**
 * A single parsed SSE frame.  Only the {@code data:} field is used by ORB.
 */
public record SseFrame(String data) {

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
