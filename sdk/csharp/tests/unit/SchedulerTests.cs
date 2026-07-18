// Unit tests for the Scheduler enum and its wire mapping.

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
}
