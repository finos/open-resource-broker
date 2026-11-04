// Unit tests for the SSE frame parser.

using System.Text;
using FINOS.OpenResourceBroker;
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

    private const string InProgress =
        "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"in_progress\"}]}\n\n";

    // Consume a status stream the way OrbClient.StreamRequestStatusAsync does.
    private static async Task<List<string>> ConsumeAsync(string wire)
    {
        var statuses = new List<string>();
        var stream = new MemoryStream(Encoding.UTF8.GetBytes(wire));
        await foreach (var frame in SseFrameParser.ParseAsync(stream))
        {
            SseOutcome.ThrowIfFailed(frame);
            if (frame.Data.Trim() == "{}") break;
            var payload = System.Text.Json.JsonSerializer.Deserialize<SsePayload>(frame.Data);
            foreach (var req in payload?.Requests ?? new List<SseRequest>())
            {
                statuses.Add(req.Status);
                if (TerminalStatuses.All.Contains(req.Status)) return statuses;
            }
        }
        return statuses;
    }

    [Fact]
    public async Task TimeoutFrame_RaisesDistinctTimeoutError()
    {
        var wire = InProgress + "event: timeout\ndata: {\"status\": \"timeout\"}\n\ndata: {}\n\n";
        var ex = await Assert.ThrowsAsync<OrbStreamTimeoutException>(() => ConsumeAsync(wire));
        Assert.IsAssignableFrom<OrbException>(ex);
    }

    [Fact]
    public async Task ErrorFrame_RaisesError()
    {
        var wire = InProgress + "event: error\ndata: {\"error\": \"internal\"}\n\ndata: {}\n\n";
        await Assert.ThrowsAsync<OrbStreamException>(() => ConsumeAsync(wire));
    }

    [Fact]
    public async Task CompleteFrame_IsNotAnError()
    {
        var wire = InProgress + "event: complete\ndata: {\"status\": \"failed\"}\n\ndata: {}\n\n";
        Assert.Equal(new List<string> { "in_progress" }, await ConsumeAsync(wire));
    }

    [Fact]
    public async Task CompletedStream_KeepsFinalStatusReadable()
    {
        var complete = "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"complete\"}]}\n\n";
        var wire = InProgress + complete
            + "event: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n";
        var statuses = await ConsumeAsync(wire);
        Assert.Equal("complete", statuses[^1]);
    }

    [Fact]
    public async Task OldServerWithoutNamedFrames_BehavesAsBefore()
    {
        var failed = "data: {\"requests\":[{\"request_id\":\"r1\",\"status\":\"failed\"}]}\n\n";
        Assert.Equal(
            new List<string> { "in_progress", "failed" },
            await ConsumeAsync(InProgress + failed + "data: {}\n\n"));
        Assert.Equal(
            new List<string> { "in_progress" },
            await ConsumeAsync(InProgress + "data: {}\n\n"));
    }

    // Output of the server's default scheduler formatter for a request status
    // stream frame: numeric launch_time, *_ip_address keys, a null launch_time.
    private const string Golden = "{\"requests\":[{\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"status\":\"complete\",\"template_id\":\"t1\",\"requested_count\":2,\"created_at\":\"2026-10-31T01:00:00Z\",\"machine_ids\":[],\"message\":\"done\",\"metadata\":{},\"request_type\":\"acquire\",\"desired_capacity\":1,\"successful_count\":2,\"failed_count\":0,\"error_details\":{},\"provider_data\":{},\"version\":0,\"resource_ids\":[],\"machines\":[{\"machine_id\":\"i-0abc123def4567890\",\"name\":\"ip-10-0-1-12\",\"result\":\"succeed\",\"status\":\"running\",\"private_ip_address\":\"10.0.1.12\",\"public_ip_address\":\"54.12.34.56\",\"instance_type\":\"t3.micro\",\"price_type\":\"ondemand\",\"cloud_host_id\":\"ip-10-0-1-12.ec2.internal\",\"launch_time\":1793411323,\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\"},{\"machine_id\":\"i-0def456abc7890123\",\"name\":\"ip-10-0-1-13\",\"result\":\"executing\",\"status\":\"pending\",\"request_id\":\"req-3f8a1c2e-5b7d-4e91-a0c6-9d2b8e4f1a73\",\"message\":\"\",\"launch_time\":null,\"cloud_host_id\":null}]}],\"message\":\"Request status retrieved successfully\",\"count\":1}";

    [Fact]
    public void GoldenServerPayload_ParsesStatusLaunchTimeAndIps()
    {
        var payload = System.Text.Json.JsonSerializer.Deserialize<SsePayload>(Golden);
        var req = Assert.Single(payload!.Requests!);
        Assert.Equal("complete", req.Status);
        Assert.Equal(2, req.Machines!.Count);
        Assert.Equal("10.0.1.12", req.Machines[0].PrivateIp);
        Assert.Equal("54.12.34.56", req.Machines[0].PublicIp);
        Assert.Equal("1793411323", req.Machines[0].LaunchTime);
        Assert.Null(req.Machines[1].LaunchTime);
    }

    [Fact]
    public async Task GoldenCompletedStream_KeepsFinalStatusReadable()
    {
        var wire = "data: " + Golden + "\n\nevent: complete\ndata: {\"status\": \"complete\"}\n\ndata: {}\n\n";
        var statuses = await ConsumeAsync(wire);
        Assert.Equal("complete", statuses[^1]);
    }
}
