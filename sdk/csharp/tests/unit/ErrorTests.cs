// Unit tests for the error types.

using System.Net;
using System.Net.Sockets;
using System.Text;
using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Transport;
using Xunit;

namespace UnitTests;

public class ErrorTests
{
    [Fact]
    public void OrbApiException_StoresStatusCode()
    {
        var ex = new OrbApiException(404, "Not Found");
        Assert.Equal(404, ex.StatusCode);
        Assert.Equal("Not Found", ex.Message);
    }

    [Fact]
    public void OrbApiException_StoresCode()
    {
        var ex = new OrbApiException(400, "Bad Request", "INVALID_TEMPLATE");
        Assert.Equal("INVALID_TEMPLATE", ex.Code);
    }

    [Fact]
    public void OrbNotFoundException_Is404()
    {
        var ex = new OrbNotFoundException("template not found");
        Assert.Equal(404, ex.StatusCode);
        Assert.IsAssignableFrom<OrbApiException>(ex);
    }

    [Fact]
    public void OrbUnavailableException_IsOrbException()
    {
        var ex = new OrbUnavailableException("process died");
        Assert.IsAssignableFrom<OrbException>(ex);
    }

    [Fact]
    public void OrbApiException_ToStringIncludesStatusCode()
    {
        var ex = new OrbApiException(500, "Internal Server Error");
        Assert.Contains("500", ex.ToString());
    }

    [Fact]
    public void OrbApiException_CarriesRequestId()
    {
        var ex = new OrbApiException(500, "boom", requestId: "req-123");
        Assert.Equal("req-123", ex.RequestId);
    }

    [Theory]
    [InlineData(401, typeof(OrbUnauthorizedException))]
    [InlineData(403, typeof(OrbForbiddenException))]
    [InlineData(404, typeof(OrbNotFoundException))]
    [InlineData(409, typeof(OrbConflictException))]
    [InlineData(408, typeof(OrbTimeoutException))]
    [InlineData(503, typeof(OrbUnavailableException))]
    public void ForStatus_ConstructsTypedSentinel(int status, Type expected)
    {
        var ex = OrbApiException.ForStatus(status, "msg", "CODE", "{}", "req-1");
        Assert.IsType(expected, ex);
        Assert.Equal(status, ex.StatusCode);
        Assert.Equal("CODE", ex.Code);
        Assert.Equal("req-1", ex.RequestId);
        Assert.Equal("{}", ex.ResponseBody);
    }

    [Fact]
    public void ForStatus_FallsBackToBaseForUntypedStatus()
    {
        var ex = OrbApiException.ForStatus(500, "server error");
        Assert.Equal(typeof(OrbApiException), ex.GetType());
        Assert.Equal(500, ex.StatusCode);
    }

    [Fact]
    public void TypedSentinels_AreCatchableAsApiExceptionAndBase()
    {
        var ex = OrbApiException.ForStatus(404, "nope");
        Assert.IsAssignableFrom<OrbApiException>(ex);
        Assert.IsAssignableFrom<OrbException>(ex);
        Assert.True(((OrbApiException)ex).IsNotFound);
    }

    [Fact]
    public void OrbUnavailableException_Is503AndApiException()
    {
        var ex = new OrbUnavailableException("process died");
        Assert.Equal(503, ex.StatusCode);
        Assert.True(ex.IsUnavailable);
        Assert.IsAssignableFrom<OrbApiException>(ex);
        Assert.IsAssignableFrom<OrbException>(ex);
    }

    // An error body whose code/message is not a string, or is not JSON at all,
    // must still map to an OrbApiException rather than escaping as a parse error.
    [Theory]
    [InlineData("{\"code\": 5}")]
    [InlineData("{\"message\": 5}")]
    [InlineData("not json")]
    public async Task UnparseableErrorBody_StillYieldsOrbApiException(string body)
    {
        using var listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        listener.Bind(new IPEndPoint(IPAddress.Loopback, 0));
        listener.Listen(1);
        var port = ((IPEndPoint)listener.LocalEndPoint!).Port;
        var serve = Task.Run(async () =>
        {
            using var conn = await listener.AcceptAsync();
            using var stream = new NetworkStream(conn);
            var buf = new byte[4096];
            _ = await stream.ReadAsync(buf);
            var payload = Encoding.UTF8.GetBytes(body);
            var head = Encoding.ASCII.GetBytes(
                $"HTTP/1.1 400 Bad Request\r\nContent-Length: {payload.Length}\r\nConnection: close\r\n\r\n");
            await stream.WriteAsync(head);
            await stream.WriteAsync(payload);
        });

        await using var client = await OrbClient.CreateAsync(new ClientConfig
        {
            BaseUrl = $"http://127.0.0.1:{port}",
            Retry = new RetryConfig { MaxRetries = 0 },
        });
        var ex = await Assert.ThrowsAsync<OrbApiException>(() => client.HealthAsync());
        Assert.Equal(400, ex.StatusCode);
        Assert.Null(ex.Code);
        await serve;
    }
}
