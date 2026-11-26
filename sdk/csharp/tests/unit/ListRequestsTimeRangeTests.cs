// listRequests forwards the creation-time range as start_time / end_time.

using System.Net;
using System.Net.Sockets;
using System.Text;
using FINOS.OpenResourceBroker;
using Xunit;

namespace UnitTests;

public class ListRequestsTimeRangeTests
{
    // Serves `count` sequential connections with an empty list body and returns
    // each request line (e.g. "GET /api/v1/requests/?start_time=... HTTP/1.1").
    private static (int Port, Task<List<string>> Lines) Serve(int count)
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var lines = Task.Run(async () =>
        {
            var seen = new List<string>();
            try
            {
                for (var i = 0; i < count; i++)
                {
                    using var conn = await listener.AcceptTcpClientAsync();
                    using var stream = conn.GetStream();
                    var buf = new byte[8192];
                    var n = await stream.ReadAsync(buf);
                    var head = Encoding.ASCII.GetString(buf, 0, n);
                    seen.Add(head.Split("\r\n")[0]);
                    var payload = Encoding.UTF8.GetBytes("{\"requests\":[]}");
                    var resp = Encoding.ASCII.GetBytes(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" +
                        $"Content-Length: {payload.Length}\r\nConnection: close\r\n\r\n");
                    await stream.WriteAsync(resp);
                    await stream.WriteAsync(payload);
                }
            }
            finally
            {
                listener.Stop();
            }
            return seen;
        });
        return (port, lines);
    }

    [Fact]
    public async Task Range_Is_Sent_As_Utc_With_Six_Fraction_Digits_And_Omitted_When_Unset()
    {
        var (port, lines) = Serve(2);
        await using var client = await OrbClient.CreateAsync(new ClientConfig
        {
            BaseUrl = $"http://127.0.0.1:{port}",
            Retry = new RetryConfig { MaxRetries = 0 },
        });

        // +02:00 offset: the wire value must be the equivalent UTC instant
        var start = new DateTimeOffset(2026, 11, 1, 12, 0, 0, 500, TimeSpan.FromHours(2));
        var end = new DateTimeOffset(2026, 11, 2, 0, 0, 0, TimeSpan.Zero);
        await client.ListRequestsAsync(startTime: start, endTime: end);
        await client.ListRequestsAsync(status: "pending");

        var seen = await lines;
        var ranged = Uri.UnescapeDataString(seen[0]);
        Assert.Contains("start_time=2026-11-01T10:00:00.500000Z", ranged);
        Assert.Contains("end_time=2026-11-02T00:00:00.000000Z", ranged);
        Assert.DoesNotContain("start_time", seen[1]);
        Assert.DoesNotContain("end_time", seen[1]);
    }
}
