// Unit tests for the retry handler.

using System.Net;
using System.Net.Sockets;
using FINOS.OpenResourceBroker.Transport;
using Xunit;

namespace UnitTests;

public class RetryTests
{
    [Theory]
    [InlineData(429)]
    [InlineData(503)]
    public async Task RetryHandler_RetriesOn429And503_ForIdempotentMethods(int statusCode)
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return attempts < 3
                ? new HttpResponseMessage((HttpStatusCode)statusCode)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") };
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.GetAsync("/test"); // GET is idempotent

        Assert.Equal(HttpStatusCode.OK, resp.StatusCode);
        Assert.Equal(3, attempts);
    }

    // Non-idempotent POST must NOT be retried on 429/503: the server may have
    // already processed a request whose response was lost, so retrying risks a
    // duplicate (double-provisioning) side effect.
    [Theory]
    [InlineData(429)]
    [InlineData(503)]
    public async Task RetryHandler_DoesNotRetry429Or503_ForPostMethod(int statusCode)
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return new HttpResponseMessage((HttpStatusCode)statusCode);
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.PostAsync("/test", new StringContent("{}"));

        Assert.Equal((HttpStatusCode)statusCode, resp.StatusCode);
        Assert.Equal(1, attempts); // no retry
    }

    [Theory]
    [InlineData(500)]
    [InlineData(502)]
    [InlineData(504)]
    public async Task RetryHandler_RetriesOn5xx_ForIdempotentMethods(int statusCode)
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return attempts < 2
                ? new HttpResponseMessage((HttpStatusCode)statusCode)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") };
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.GetAsync("/test");

        Assert.Equal(HttpStatusCode.OK, resp.StatusCode);
        Assert.Equal(2, attempts);
    }

    [Theory]
    [InlineData(500)]
    [InlineData(502)]
    public async Task RetryHandler_DoesNotRetry5xx_ForPostMethod(int statusCode)
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return new HttpResponseMessage((HttpStatusCode)statusCode);
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.PostAsync("/test", new StringContent("{}"));

        Assert.Equal((HttpStatusCode)statusCode, resp.StatusCode);
        Assert.Equal(1, attempts); // no retry
    }

    // A post-write network drop (generic IOException) is NOT retried for POST:
    // the request may have reached the server before the socket dropped.
    [Fact]
    public async Task RetryHandler_DoesNotRetryNetworkError_ForPostMethod()
    {
        var attempts = 0;
        var inner = new ThrowingHandler(() =>
        {
            attempts++;
            throw new HttpRequestException("connection reset", new IOException("reset"));
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        await Assert.ThrowsAsync<HttpRequestException>(
            () => client.PostAsync("/test", new StringContent("{}")));

        Assert.Equal(1, attempts); // no retry — request may have been processed
    }

    // A pre-write connection-refused failure IS retried for POST: the connection
    // was never established, so the server is guaranteed not to have seen it.
    [Fact]
    public async Task RetryHandler_RetriesConnectionRefused_ForPostMethod()
    {
        var attempts = 0;
        var inner = new ThrowingHandler(() =>
        {
            attempts++;
            if (attempts < 2)
                throw new HttpRequestException(
                    "refused", new SocketException((int)SocketError.ConnectionRefused));
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") };
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.PostAsync("/test", new StringContent("{}"));

        Assert.Equal(HttpStatusCode.OK, resp.StatusCode);
        Assert.Equal(2, attempts); // 1 refused + 1 success
    }

    // A server-side request timeout (TaskCanceledException with the caller's
    // token NOT cancelled) is transient for idempotent methods.
    [Fact]
    public async Task RetryHandler_RetriesRequestTimeout_ForIdempotentMethods()
    {
        var attempts = 0;
        var inner = new ThrowingHandler(() =>
        {
            attempts++;
            if (attempts < 2)
                throw new TaskCanceledException("request timed out");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") };
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.GetAsync("/test");

        Assert.Equal(HttpStatusCode.OK, resp.StatusCode);
        Assert.Equal(2, attempts);
    }

    [Fact]
    public async Task RetryHandler_DoesNotRetry4xx_Except429()
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return new HttpResponseMessage(HttpStatusCode.BadRequest);
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 3, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.GetAsync("/test");

        Assert.Equal(HttpStatusCode.BadRequest, resp.StatusCode);
        Assert.Equal(1, attempts);
    }

    [Fact]
    public async Task RetryHandler_StopsAfterMaxRetries()
    {
        var attempts = 0;
        var inner = new CountingHandler(() =>
        {
            attempts++;
            return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable);
        });

        var handler = new RetryDelegatingHandler(
            new RetryConfig { MaxRetries = 2, BaseDelayMs = 1, MaxDelayMs = 10 }, inner);

        using var client = new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
        var resp = await client.GetAsync("/test");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, resp.StatusCode);
        Assert.Equal(3, attempts); // 1 initial + 2 retries
    }

    private sealed class CountingHandler : HttpMessageHandler
    {
        private readonly Func<HttpResponseMessage> _factory;
        public CountingHandler(Func<HttpResponseMessage> factory) => _factory = factory;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken ct)
            => Task.FromResult(_factory());
    }

    // Handler whose factory may throw (to simulate network failures) or return a
    // response.
    private sealed class ThrowingHandler : HttpMessageHandler
    {
        private readonly Func<HttpResponseMessage> _factory;
        public ThrowingHandler(Func<HttpResponseMessage> factory) => _factory = factory;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken ct)
        {
            try { return Task.FromResult(_factory()); }
            catch (Exception ex) { return Task.FromException<HttpResponseMessage>(ex); }
        }
    }
}
