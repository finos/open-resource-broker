// Unit tests for the Scheduler enum and its wire mapping.

using System.Net.Http;
using FINOS.OpenResourceBroker;
using Xunit;

namespace UnitTests;

public class SchedulerTests
{
    [Fact]
    public void WireValue_MapsEnumToHeaderValue()
    {
        Assert.Equal("default", Scheduler.Default.WireValue());
        Assert.Equal("hostfactory", Scheduler.HostFactory.WireValue());
    }

    [Theory]
    [InlineData(null, Scheduler.Default)]
    [InlineData("", Scheduler.Default)]
    [InlineData("default", Scheduler.Default)]
    [InlineData("DEFAULT", Scheduler.Default)]
    [InlineData("hostfactory", Scheduler.HostFactory)]
    [InlineData("HostFactory", Scheduler.HostFactory)]
    public void FromWire_ParsesCaseInsensitively(string? value, Scheduler expected)
    {
        Assert.Equal(expected, SchedulerExtensions.FromWire(value));
    }

    [Fact]
    public void FromWire_ThrowsOnUnknownValue()
    {
        Assert.Throws<ArgumentException>(() => SchedulerExtensions.FromWire("bogus"));
    }

    private static HttpResponseMessage ResponseNaming(string? format)
    {
        var response = new HttpResponseMessage();
        if (format != null)
            response.Headers.TryAddWithoutValidation("X-ORB-Scheduler", format);
        return response;
    }

    [Fact]
    public void ThrowIfMismatch_ResponseNamingAnotherFormat_Throws()
    {
        var error = Assert.Throws<SchedulerMismatchException>(() =>
            SchedulerFormat.ThrowIfMismatch(Scheduler.Default, ResponseNaming("hostfactory")));

        Assert.Equal("default", error.Expected);
        Assert.Equal("hostfactory", error.Actual);
    }

    [Fact]
    public void ThrowIfMismatch_ResponseWithoutHeader_IsAccepted()
    {
        SchedulerFormat.ThrowIfMismatch(Scheduler.HostFactory, ResponseNaming(null));
    }

    [Theory]
    [InlineData("hostfactory")]
    [InlineData("HostFactory")]
    public void ThrowIfMismatch_ResponseNamingTheRequestedFormat_IsAccepted(string format)
    {
        SchedulerFormat.ThrowIfMismatch(Scheduler.HostFactory, ResponseNaming(format));
    }
}
