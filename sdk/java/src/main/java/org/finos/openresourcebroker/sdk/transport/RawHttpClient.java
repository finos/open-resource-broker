// Layer 2: UDS + TCP/TLS Transport
package org.finos.openresourcebroker.sdk.transport;

import org.finos.openresourcebroker.sdk.client.OrbApiException;

import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;
import java.io.*;
import java.net.URI;
import java.nio.channels.Channels;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.function.Supplier;

/**
 * Minimal HTTP/1.1 client that can use either a Unix domain socket (SocketChannel)
 * or raw TCP sockets (plaintext for {@code http://}, TLS for {@code https://}).
 *
 * <p>The UDS path uses SocketChannel directly because java.net.http.HttpClient
 * does not support Unix domain sockets natively (as of Java 17).  HTTP/1.1
 * over UDS with keep-alive disabled (uvicorn closes after each request) is
 * simple enough to implement in ~200 lines.
 *
 * <p>The remote TCP path honours the URL scheme: {@code https://} connects with
 * an {@link SSLSocket} (default port 443, certificate + hostname verification
 * enabled), so Bearer tokens and SigV4 Authorization headers are never sent in
 * cleartext.  {@code http://} uses a plaintext socket (default port 80) for local
 * development only.
 *
 * <p>This class handles GET/POST/PUT/DELETE for JSON responses and raw text
 * (metrics endpoint).  SSE is handled by {@link #openSseStream}.
 *
 * <p>Response bodies are bounded: declared Content-Length and chunk sizes are
 * capped against {@link #MAX_RESPONSE_BYTES} and read incrementally, so a hostile
 * or buggy server cannot force an unbounded allocation.
 *
 * <p>Per-request signing (e.g. AWS SigV4) is supported via
 * {@link #setPerRequestSigner(PerRequestSigner)} and per-request dynamic headers
 * (e.g. a refreshing Bearer token) via
 * {@link #setPerRequestHeaderProvider(Supplier)}.  Both are invoked immediately
 * before writing each request — including SSE streams — so that the
 * Authorization / x-amz-date headers reflect the actual URI, body, and current
 * credentials.
 */
public class RawHttpClient {

    /**
     * Maximum response body size (64 MiB).  Declared Content-Length values and
     * chunk sizes larger than this are rejected rather than allocated, so an
     * attacker-controlled header cannot OOM the client.
     */
    public static final int MAX_RESPONSE_BYTES = 64 * 1024 * 1024;

    /**
     * Callback invoked once per outgoing request to compute authentication headers
     * that depend on the full URI and body (e.g. AWS SigV4).
     *
     * <p>The returned map is merged into the request headers after all static
     * headers (including Content-Type and Content-Length) have been computed, but
     * before the bytes are written to the socket.
     */
    @FunctionalInterface
    public interface PerRequestSigner {
        /**
         * @param method HTTP method (GET, POST, PUT, DELETE)
         * @param uri    full URI (scheme + host + path + query) for signing purposes
         * @param body   raw request body bytes; empty array for requests without a body
         * @return map of headers to add (e.g. Authorization, x-amz-date); never null
         */
        Map<String, String> sign(String method, URI uri, byte[] body);
    }

    private final String socketPath; // null if TCP mode
    private final String baseUrl;    // used in TCP mode
    private final Duration connectTimeout;
    private final Duration readTimeout;
    private final boolean tls;       // true when baseUrl scheme is https

    // Extra headers applied to every request (e.g. X-ORB-Scheduler)
    private final List<String[]> defaultHeaders = new ArrayList<>();

    // Optional per-request signer (e.g. AwsSigV4Auth); null if not configured
    private PerRequestSigner perRequestSigner;

    // Optional per-request dynamic header provider (e.g. refreshing Bearer token);
    // invoked on EVERY request so token rotation takes effect. null if not set.
    private Supplier<Map<String, String>> perRequestHeaderProvider;

    // Retry config
    private int maxRetries = 3;
    private Duration baseDelay = Duration.ofMillis(500);

    public RawHttpClient(String socketPath, String baseUrl, Duration connectTimeout, Duration readTimeout) {
        this.socketPath = socketPath;
        this.baseUrl = baseUrl;
        this.connectTimeout = connectTimeout != null ? connectTimeout : Duration.ofSeconds(10);
        this.readTimeout = readTimeout != null ? readTimeout : Duration.ofSeconds(30);
        boolean isTls = false;
        if (socketPath == null && baseUrl != null) {
            String scheme = URI.create(baseUrl).getScheme();
            isTls = "https".equalsIgnoreCase(scheme);
        }
        this.tls = isTls;
    }

    public void addDefaultHeader(String name, String value) {
        defaultHeaders.add(new String[]{name, value});
    }

    /** Set a per-request signer (e.g. for AWS SigV4). Pass {@code null} to disable. */
    public void setPerRequestSigner(PerRequestSigner signer) {
        this.perRequestSigner = signer;
    }

    /**
     * Set a per-request dynamic header provider (e.g. a refreshing Bearer token).
     * The supplier is invoked on every outgoing request, so token rotation takes
     * effect immediately.  Pass {@code null} to disable.
     */
    public void setPerRequestHeaderProvider(Supplier<Map<String, String>> provider) {
        this.perRequestHeaderProvider = provider;
    }

    public void setMaxRetries(int maxRetries) {
        this.maxRetries = maxRetries;
    }

    public void setBaseDelay(Duration baseDelay) {
        this.baseDelay = baseDelay;
    }

    /** Perform GET request, returning the response body as a String. */
    public HttpResult get(String path, Map<String, String> params) throws IOException, InterruptedException {
        String fullPath = buildPath(path, params);
        return executeWithRetry("GET", fullPath, null, "application/json");
    }

    /**
     * Perform a single-attempt GET with no retry logic.
     *
     * <p>Used by {@code health()}: a degraded {@code 503} response must be
     * observed directly and returned to the caller, never retried away.
     */
    public HttpResult getNoRetry(String path, Map<String, String> params) throws IOException {
        String fullPath = buildPath(path, params);
        return execute("GET", fullPath, null, "application/json", true);
    }

    /** Perform GET request for plain text (e.g. metrics). */
    public HttpResult getText(String path) throws IOException, InterruptedException {
        return executeWithRetry("GET", path, null, "text/plain, */*");
    }

    /** Perform POST request with JSON body. */
    public HttpResult post(String path, String jsonBody) throws IOException, InterruptedException {
        return executeWithRetry("POST", path, jsonBody, "application/json");
    }

    /** Perform PUT request with JSON body. */
    public HttpResult put(String path, String jsonBody) throws IOException, InterruptedException {
        return executeWithRetry("PUT", path, jsonBody, "application/json");
    }

    /** Perform DELETE request. */
    public HttpResult delete(String path) throws IOException, InterruptedException {
        return executeWithRetry("DELETE", path, null, "application/json");
    }

    /** Open an SSE stream (returns raw InputStream; caller must close). */
    public InputStream openSseStream(String path) throws IOException {
        if (socketPath != null) {
            return openSseStreamUds(path);
        } else {
            return openSseStreamTcp(path);
        }
    }

    // ------------------------------------------------------------------
    // Retry logic
    // ------------------------------------------------------------------

    /**
     * Reports whether an HTTP method may be safely retried after a transient
     * failure.  POST is excluded: a provisioning POST may already have been
     * applied server-side before the failure, so retrying risks duplicating the
     * operation.  Mirrors the Go/TS convergence (GET/PUT/DELETE/HEAD only).
     */
    private static boolean isIdempotent(String method) {
        return switch (method) {
            case "GET", "PUT", "DELETE", "HEAD" -> true;
            default -> false;
        };
    }

    private HttpResult executeWithRetry(String method, String path, String body, String accept)
            throws IOException, InterruptedException {
        IOException lastIoErr = null;
        HttpResult lastResult = null;
        boolean firstAttempt = true;
        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            if (attempt > 0) {
                long delayMs = baseDelay.toMillis() * (1L << (attempt - 1));
                Thread.sleep(delayMs);
            }
            try {
                lastResult = execute(method, path, body, accept, firstAttempt);
                if (!shouldRetryStatus(method, lastResult.statusCode())) {
                    return lastResult;
                }
                // retryable status — continue
            } catch (PreWriteConnectException e) {
                // Connection refused before any bytes were written: the server
                // never saw the request, so it is safe to retry even for POST.
                lastIoErr = e;
            } catch (IOException e) {
                lastIoErr = e;
                if (!isIdempotent(method)) {
                    // Non-idempotent (POST): a post-write network error may mean the
                    // request was already processed. Never blind-retry — fail loud.
                    throw e;
                }
                // idempotent — retry
            }
            firstAttempt = false;
        }
        if (lastIoErr != null) throw lastIoErr;
        return lastResult;
    }

    /**
     * Retry policy for HTTP status codes.  429/503 and other 5xx are retried
     * ONLY for idempotent methods; a POST is never retried on these because the
     * server may have processed it before failing to respond.  4xx (except 429)
     * is never retried.  Mirrors the Go/TS convergence.
     */
    private static boolean shouldRetryStatus(String method, int status) {
        if (!isIdempotent(method)) return false;
        if (status == 429 || status == 503) return true;
        return status >= 500;
    }

    // ------------------------------------------------------------------
    // Core HTTP execution
    // ------------------------------------------------------------------

    private HttpResult execute(String method, String path, String body, String accept,
                               boolean firstAttempt)
            throws IOException {
        if (socketPath != null) {
            return executeUds(method, path, body, accept);
        } else {
            return executeTcp(method, path, body, accept, firstAttempt);
        }
    }

    private HttpResult executeUds(String method, String path, String body, String accept)
            throws IOException {
        java.net.UnixDomainSocketAddress addr =
                java.net.UnixDomainSocketAddress.of(socketPath);
        try (SocketChannel ch = SocketChannel.open(addr)) {
            ch.configureBlocking(true);
            return doHttp11(ch, method, "localhost", path, body, accept);
        }
    }

    private HttpResult executeTcp(String method, String path, String body, String accept,
                                  boolean firstAttempt)
            throws IOException {
        URI uri = URI.create(baseUrl + path);
        String host = uri.getHost();
        int port = resolvePort(uri);
        java.net.Socket sock = null;
        try {
            sock = openTcpSocket(host, port, firstAttempt);
            return doHttp11Streams(sock.getInputStream(), sock.getOutputStream(),
                    method, host, path, body, accept);
        } finally {
            if (sock != null) {
                try { sock.close(); } catch (IOException ignored) {}
            }
        }
    }

    /** Default port for the scheme when the URL omits one (443 for TLS, 80 otherwise). */
    private int resolvePort(URI uri) {
        if (uri.getPort() != -1) return uri.getPort();
        return tls ? 443 : 80;
    }

    /**
     * Open a connected TCP socket, wrapped in TLS with certificate + hostname
     * verification when the base URL uses {@code https://}.
     *
     * <p>A connection-refused failure on the first write attempt is surfaced as a
     * {@link PreWriteConnectException} so the retry layer knows the request never
     * reached the server (the only network failure safe to retry for POST).
     */
    private java.net.Socket openTcpSocket(String host, int port, boolean firstAttempt)
            throws IOException {
        java.net.Socket plain = new java.net.Socket();
        try {
            plain.connect(new java.net.InetSocketAddress(host, port),
                    (int) connectTimeout.toMillis());
        } catch (java.net.ConnectException e) {
            try { plain.close(); } catch (IOException ignored) {}
            // Connection refused: the server never received any bytes.
            throw new PreWriteConnectException(e.getMessage(), e);
        }
        plain.setSoTimeout((int) readTimeout.toMillis());

        if (!tls) {
            return plain;
        }

        // Upgrade to TLS with certificate + hostname verification.
        SSLSocketFactory factory = (SSLSocketFactory) SSLSocketFactory.getDefault();
        SSLSocket ssl = (SSLSocket) factory.createSocket(plain, host, port, true);
        ssl.setUseClientMode(true);
        SSLParameters params = ssl.getSSLParameters();
        // "HTTPS" enables RFC 2818 hostname verification during the handshake.
        params.setEndpointIdentificationAlgorithm("HTTPS");
        ssl.setSSLParameters(params);
        ssl.startHandshake(); // fail loud on a bad/untrusted certificate
        return ssl;
    }

    private HttpResult doHttp11(SocketChannel ch, String method, String host, String path,
                                 String body, String accept) throws IOException {
        OutputStream out = Channels.newOutputStream(ch);
        InputStream in = Channels.newInputStream(ch);
        return doHttp11Streams(in, out, method, host, path, body, accept);
    }

    private HttpResult doHttp11Streams(InputStream in, OutputStream out,
                                        String method, String host, String path,
                                        String body, String accept) throws IOException {
        byte[] bodyBytes = body != null ? body.getBytes(StandardCharsets.UTF_8) : new byte[0];

        // Compute per-request auth headers (SigV4 signer + dynamic header provider)
        Map<String, String> authHeaders = computeAuthHeaders(method, host, path, bodyBytes);

        // Build request line and static headers
        StringBuilder sb = new StringBuilder();
        sb.append(method).append(" ").append(path).append(" HTTP/1.1\r\n");
        sb.append("Host: ").append(host).append("\r\n");
        sb.append("Accept: ").append(accept).append("\r\n");
        sb.append("Connection: close\r\n");

        // Static default headers (e.g. X-ORB-Scheduler)
        for (String[] h : defaultHeaders) {
            sb.append(h[0]).append(": ").append(h[1]).append("\r\n");
        }

        // Per-request auth headers (Authorization, x-amz-date, etc.)
        for (Map.Entry<String, String> e : authHeaders.entrySet()) {
            sb.append(e.getKey()).append(": ").append(e.getValue()).append("\r\n");
        }

        if (body != null) {
            sb.append("Content-Type: application/json\r\n");
            sb.append("Content-Length: ").append(bodyBytes.length).append("\r\n");
        }
        sb.append("\r\n");

        byte[] headerBytes = sb.toString().getBytes(StandardCharsets.US_ASCII);
        out.write(headerBytes);
        if (bodyBytes.length > 0) {
            out.write(bodyBytes);
        }
        out.flush();

        return parseResponse(in);
    }

    /**
     * Compute per-request auth headers.  Combines the SigV4 signer (if any) and
     * the dynamic header provider (if any).  Returns an empty map when neither is
     * configured.  Both are invoked on every call so credential rotation and
     * token refresh take effect on each request.
     */
    private Map<String, String> computeAuthHeaders(String method, String host,
                                                    String path, byte[] bodyBytes) {
        Map<String, String> result = new LinkedHashMap<>();
        if (perRequestHeaderProvider != null) {
            Map<String, String> dynamic = perRequestHeaderProvider.get();
            if (dynamic != null) result.putAll(dynamic);
        }
        if (perRequestSigner != null) {
            try {
                String scheme = (socketPath != null) ? "http" : URI.create(baseUrl).getScheme();
                URI uri = new URI(scheme, null, host, -1,
                        extractPathOnly(path), extractQuery(path), null);
                Map<String, String> signed = perRequestSigner.sign(method, uri, bodyBytes);
                if (signed != null) result.putAll(signed);
            } catch (Exception e) {
                throw new RuntimeException("Per-request signing failed: " + e.getMessage(), e);
            }
        }
        return result;
    }

    /** Extract just the path component from a path-with-optional-query string. */
    private static String extractPathOnly(String pathAndQuery) {
        int q = pathAndQuery.indexOf('?');
        return q == -1 ? pathAndQuery : pathAndQuery.substring(0, q);
    }

    /** Extract the raw query string (without '?') from a path, or null. */
    private static String extractQuery(String pathAndQuery) {
        int q = pathAndQuery.indexOf('?');
        return q == -1 ? null : pathAndQuery.substring(q + 1);
    }

    private HttpResult parseResponse(InputStream in) throws IOException {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8));

        // Status line
        String statusLine = reader.readLine();
        if (statusLine == null) throw new IOException("Empty response from server");

        // e.g. "HTTP/1.1 200 OK"
        String[] parts = statusLine.split(" ", 3);
        if (parts.length < 2) throw new IOException("Bad status line: " + statusLine);
        int statusCode = Integer.parseInt(parts[1].trim());

        // Headers
        Map<String, String> headers = new LinkedHashMap<>();
        String line;
        while ((line = reader.readLine()) != null && !line.isEmpty()) {
            int colon = line.indexOf(':');
            if (colon > 0) {
                String name = line.substring(0, colon).trim().toLowerCase(java.util.Locale.ROOT);
                String value = line.substring(colon + 1).trim();
                headers.put(name, value);
            }
        }

        // Body — bounded reads guard against attacker-controlled sizes
        StringBuilder bodyBuilder = new StringBuilder();
        String contentLength = headers.get("content-length");
        if (contentLength != null) {
            long len = Long.parseLong(contentLength.trim());
            if (len < 0 || len > MAX_RESPONSE_BYTES) {
                throw new IOException("Response Content-Length " + len +
                        " exceeds maximum allowed " + MAX_RESPONSE_BYTES);
            }
            bodyBuilder.append(readFixedLength(reader, (int) len));
        } else {
            String transferEncoding = headers.get("transfer-encoding");
            if ("chunked".equalsIgnoreCase(transferEncoding)) {
                bodyBuilder.append(readChunked(reader));
            } else {
                // Read until EOF, capped at MAX_RESPONSE_BYTES
                char[] buf = new char[8192];
                int n;
                long total = 0;
                while ((n = reader.read(buf)) != -1) {
                    total += n;
                    if (total > MAX_RESPONSE_BYTES) {
                        throw new IOException("Response body exceeds maximum allowed "
                                + MAX_RESPONSE_BYTES + " bytes");
                    }
                    bodyBuilder.append(buf, 0, n);
                }
            }
        }

        return new HttpResult(statusCode, bodyBuilder.toString(), headers);
    }

    /** Read exactly {@code len} chars into a fixed-size buffer, bounded by the cap. */
    private String readFixedLength(BufferedReader reader, int len) throws IOException {
        StringBuilder sb = new StringBuilder(Math.min(len, 8192));
        char[] buf = new char[Math.min(len, 8192)];
        int remaining = len;
        while (remaining > 0) {
            int n = reader.read(buf, 0, Math.min(remaining, buf.length));
            if (n == -1) break;
            sb.append(buf, 0, n);
            remaining -= n;
        }
        return sb.toString();
    }

    private String readChunked(BufferedReader reader) throws IOException {
        StringBuilder sb = new StringBuilder();
        String sizeLine;
        long total = 0;
        while ((sizeLine = reader.readLine()) != null) {
            // Chunk-size line may carry extensions after ';'
            String hex = sizeLine.trim();
            int semi = hex.indexOf(';');
            if (semi >= 0) hex = hex.substring(0, semi).trim();
            if (hex.isEmpty()) continue;
            long chunkSize = Long.parseLong(hex, 16);
            if (chunkSize == 0) break;
            if (chunkSize < 0 || chunkSize > MAX_RESPONSE_BYTES) {
                throw new IOException("Chunk size " + chunkSize +
                        " exceeds maximum allowed " + MAX_RESPONSE_BYTES);
            }
            total += chunkSize;
            if (total > MAX_RESPONSE_BYTES) {
                throw new IOException("Chunked response exceeds maximum allowed "
                        + MAX_RESPONSE_BYTES + " bytes");
            }
            sb.append(readFixedLength(reader, (int) chunkSize));
            reader.readLine(); // trailing \r\n after chunk
        }
        return sb.toString();
    }

    // ------------------------------------------------------------------
    // SSE — returns raw InputStream for the body after inspecting the status
    // ------------------------------------------------------------------

    private InputStream openSseStreamUds(String path) throws IOException {
        java.net.UnixDomainSocketAddress addr =
                java.net.UnixDomainSocketAddress.of(socketPath);
        SocketChannel ch = SocketChannel.open(addr);
        ch.configureBlocking(true);
        OutputStream out = Channels.newOutputStream(ch);
        writeSseRequest(out, "localhost", path);
        InputStream rawIn = Channels.newInputStream(ch);
        BufferedInputStream buffered = new BufferedInputStream(rawIn, 65536);
        checkSseResponseStatus(buffered);
        return buffered;
    }

    private InputStream openSseStreamTcp(String path) throws IOException {
        URI uri = URI.create(baseUrl + path);
        String host = uri.getHost();
        int port = resolvePort(uri);
        java.net.Socket sock = openTcpSocket(host, port, false);
        try {
            writeSseRequest(sock.getOutputStream(), host, path);
            BufferedInputStream buffered = new BufferedInputStream(sock.getInputStream(), 65536);
            checkSseResponseStatus(buffered);
            // Wrap so closing the returned stream also closes the socket.
            return new SocketBackedInputStream(buffered, sock);
        } catch (IOException e) {
            try { sock.close(); } catch (IOException ignored) {}
            throw e;
        }
    }

    private void writeSseRequest(OutputStream out, String host, String path) throws IOException {
        Map<String, String> authHeaders = computeAuthHeaders("GET", host, path, new byte[0]);
        StringBuilder sb = new StringBuilder();
        sb.append("GET ").append(path).append(" HTTP/1.1\r\n");
        sb.append("Host: ").append(host).append("\r\n");
        sb.append("Accept: text/event-stream\r\n");
        sb.append("Cache-Control: no-cache\r\n");
        sb.append("Connection: close\r\n");
        for (String[] h : defaultHeaders) {
            sb.append(h[0]).append(": ").append(h[1]).append("\r\n");
        }
        for (Map.Entry<String, String> e : authHeaders.entrySet()) {
            sb.append(e.getKey()).append(": ").append(e.getValue()).append("\r\n");
        }
        sb.append("\r\n");
        out.write(sb.toString().getBytes(StandardCharsets.US_ASCII));
        out.flush();
    }

    /**
     * Read the SSE response status line + headers.
     *
     * <p>Parses the status line and, on any status &gt;= 400, reads the (bounded)
     * error body and throws a typed {@link OrbApiException} so the caller can
     * decide: 4xx is terminal (no reconnect), 5xx is a candidate for reconnect.
     * On a 2xx the header block is consumed and the stream is left positioned at
     * the first SSE frame.
     */
    private void checkSseResponseStatus(InputStream in) throws IOException {
        String statusLine = readAsciiLine(in);
        if (statusLine == null) {
            throw new IOException("Connection closed before SSE response status line");
        }
        String[] parts = statusLine.split(" ", 3);
        if (parts.length < 2) {
            throw new IOException("Bad SSE status line: " + statusLine);
        }
        int statusCode;
        try {
            statusCode = Integer.parseInt(parts[1].trim());
        } catch (NumberFormatException e) {
            throw new IOException("Bad SSE status code: " + statusLine);
        }

        // Read remaining headers (needed to find Content-Length / request id).
        Map<String, String> headers = new LinkedHashMap<>();
        String line;
        while ((line = readAsciiLine(in)) != null && !line.isEmpty()) {
            int colon = line.indexOf(':');
            if (colon > 0) {
                headers.put(line.substring(0, colon).trim().toLowerCase(java.util.Locale.ROOT),
                        line.substring(colon + 1).trim());
            }
        }

        if (statusCode >= 400) {
            String body = readErrorBody(in, headers);
            String requestId = firstNonNull(
                    headers.get("x-request-id"), headers.get("x-correlation-id"));
            throw OrbApiException.forStatus(statusCode, null,
                    body != null && !body.isBlank() ? body : "SSE stream returned HTTP " + statusCode,
                    requestId);
        }
        // 2xx — stream is positioned at the first frame.
    }

    /** Read a bounded error body for an SSE error response (best-effort). */
    private String readErrorBody(InputStream in, Map<String, String> headers) {
        try {
            String cl = headers.get("content-length");
            int cap = 64 * 1024; // errors are small
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            int limit = cap;
            if (cl != null) {
                try { limit = Math.min(cap, Integer.parseInt(cl.trim())); }
                catch (NumberFormatException ignored) {}
            }
            int b;
            while (buf.size() < limit && (b = in.read()) != -1) {
                buf.write(b);
            }
            return buf.toString(StandardCharsets.UTF_8);
        } catch (IOException e) {
            return null;
        }
    }

    /** Read a single CRLF-terminated ASCII line from a raw byte stream. */
    private static String readAsciiLine(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        int b;
        boolean any = false;
        while ((b = in.read()) != -1) {
            any = true;
            if (b == '\n') break;
            if (b != '\r') buf.write(b);
        }
        if (!any) return null;
        return buf.toString(StandardCharsets.US_ASCII);
    }

    private static String firstNonNull(String a, String b) {
        return a != null && !a.isEmpty() ? a : b;
    }

    // ------------------------------------------------------------------
    // Path building
    // ------------------------------------------------------------------

    private String buildPath(String path, Map<String, String> params) {
        if (params == null || params.isEmpty()) return path;
        StringBuilder sb = new StringBuilder(path).append('?');
        boolean first = true;
        for (Map.Entry<String, String> e : params.entrySet()) {
            if (!first) sb.append('&');
            first = false;
            try {
                sb.append(java.net.URLEncoder.encode(e.getKey(), "UTF-8"))
                  .append('=')
                  .append(java.net.URLEncoder.encode(e.getValue(), "UTF-8"));
            } catch (UnsupportedEncodingException ex) {
                throw new RuntimeException(ex);
            }
        }
        return sb.toString();
    }

    // ------------------------------------------------------------------
    // Helper types
    // ------------------------------------------------------------------

    /** Result type carrying the HTTP status, body, and lower-cased headers. */
    public record HttpResult(int statusCode, String body, Map<String, String> headers) {}

    /**
     * Marker IOException raised when a TCP connection is refused before any bytes
     * were written.  Signals the retry layer that the request never reached the
     * server, so it is safe to retry even for a non-idempotent method.
     */
    private static final class PreWriteConnectException extends IOException {
        PreWriteConnectException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** InputStream that closes the backing socket when the stream is closed. */
    private static final class SocketBackedInputStream extends FilterInputStream {
        private final java.net.Socket socket;
        SocketBackedInputStream(InputStream in, java.net.Socket socket) {
            super(in);
            this.socket = socket;
        }
        @Override
        public void close() throws IOException {
            try {
                super.close();
            } finally {
                socket.close();
            }
        }
    }
}
