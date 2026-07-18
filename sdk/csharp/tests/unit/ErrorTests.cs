// Unit tests for the error types.

using FINOS.OpenResourceBroker;
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
}
