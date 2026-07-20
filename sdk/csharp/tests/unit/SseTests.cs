// Unit tests for the SSE frame parser.

using System.Text;
using FINOS.OpenResourceBroker.Sse;
using Xunit;

namespace UnitTests;

public class SseTests
{
    [Fact]
    public async Task ParseAsync_ParsesSingleEvent()
    {
        var wire = "data: {\"hello\":\"world\"}\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal("{\"hello\":\"world\"}", frames[0].Data);
    }

    [Fact]
    public async Task ParseAsync_ParsesMultipleEvents()
    {
        var wire = "data: first\n\ndata: second\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Equal(2, frames.Count);
        Assert.Equal("first", frames[0].Data);
        Assert.Equal("second", frames[1].Data);
    }

    [Fact]
    public async Task ParseAsync_ParsesEventId()
    {
        var wire = "id: 42\ndata: payload\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal("42", frames[0].Id);
        Assert.Equal("payload", frames[0].Data);
    }

    [Fact]
    public async Task ParseAsync_ParsesEventType()
    {
        var wire = "event: update\ndata: payload\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal("update", frames[0].Event);
    }

    [Fact]
    public async Task ParseAsync_ParsesRetry()
    {
        var wire = "retry: 3000\ndata: payload\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal(3000, frames[0].Retry);
    }

    [Fact]
    public async Task ParseAsync_IgnoresCommentLines()
    {
        var wire = ": this is a comment\ndata: payload\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal("payload", frames[0].Data);
    }

    [Fact]
    public async Task ParseAsync_SkipsEventWithNoData()
    {
        var wire = "event: ping\n\ndata: actual\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        // Event with no data field is not dispatched
        Assert.Single(frames);
        Assert.Equal("actual", frames[0].Data);
    }

    [Fact]
    public async Task ParseAsync_EmptyStream_ReturnsNoFrames()
    {
        var stream = new MemoryStream(Array.Empty<byte>());

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Empty(frames);
    }

    [Fact]
    public void TerminalStatuses_ContainsExpectedValues()
    {
        Assert.Contains("complete", TerminalStatuses.All);
        Assert.Contains("completed", TerminalStatuses.All);
        Assert.Contains("failed", TerminalStatuses.All);
        Assert.Contains("error", TerminalStatuses.All);
        Assert.Contains("cancelled", TerminalStatuses.All);
        Assert.Contains("canceled", TerminalStatuses.All);
    }

    [Fact]
    public void TerminalStatuses_IsCaseInsensitive()
    {
        Assert.Contains("COMPLETE", TerminalStatuses.All);
        Assert.Contains("Failed", TerminalStatuses.All);
    }

    [Fact]
    public async Task ParseAsync_ParsesCrlfLineTerminators()
    {
        var wire = "data: hello\r\n\r\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        var frames = new List<SseFrame>();
        await foreach (var f in SseFrameParser.ParseAsync(stream))
            frames.Add(f);

        Assert.Single(frames);
        Assert.Equal("hello", frames[0].Data);
    }

    // An oversized data frame must abort the parse with a terminal, non-retryable
    // error rather than growing the heap without bound.
    [Fact]
    public async Task ParseAsync_ThrowsOnOversizedFrame()
    {
        // One data line whose value exceeds the 4 MiB cap.
        var huge = new string('x', SseFrameParser.MaxFrameBytes + 1024);
        var wire = "data: " + huge + "\n\n";
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));

        await Assert.ThrowsAsync<SseFrameTooLargeException>(async () =>
        {
            await foreach (var _ in SseFrameParser.ParseAsync(stream)) { }
        });
    }

    // A stream that never emits a newline must not grow memory without bound.
    [Fact]
    public async Task ParseAsync_ThrowsOnUnterminatedLine()
    {
        var huge = new string('y', SseFrameParser.MaxFrameBytes + 1024);
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(huge)); // no '\n' ever

        await Assert.ThrowsAsync<SseFrameTooLargeException>(async () =>
        {
            await foreach (var _ in SseFrameParser.ParseAsync(stream)) { }
        });
    }
}
