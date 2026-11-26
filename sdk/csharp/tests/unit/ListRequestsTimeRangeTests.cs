// listRequests forwards the creation-time range as start_time / end_time.

using System.Net;
using System.Net.Sockets;
using System.Text;
using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Transport;
using Xunit;

namespace UnitTests;

public class ListRequestsTimeRangeTests
{
    [Fact]
    public async Task Range_Is_Sent_As_Utc_With_Six_Fraction_Digits_And_Omitted_When_Unset()
    {
        using var listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        listener.Bind(new IPEndPoint(IPAddress.Loopback, 0));
        listener.Listen(2);
        var port = ((IPEndPoint)listener.LocalEndPoint!).Port;

        // Serves two sequential connections with an empty list body and records
        // each request line (e.g. "GET /api/v1/requests/?start_time=... HTTP/1.1").
        var serve = Task.Run(async () =>
        {
            var seen = new List<string>();
            for (var i = 0; i < 2; i++)
            {
                using var conn = await listener.AcceptAsync();
                using var stream = new NetworkStream(conn);
                var buf = new byte[8192];
                var n = await stream.ReadAsync(buf);
                seen.Add(Encoding.ASCII.GetString(buf, 0, n).Split("\r\n")[0]);
                var payload = Encoding.UTF8.GetBytes("{\"requests\":[]}");
                var head = Encoding.ASCII.GetBytes(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" +
                    $"Content-Length: {payload.Length}\r\nConnection: close\r\n\r\n");
                await stream.WriteAsync(head);
                await stream.WriteAsync(payload);
            }
            return seen;
        });

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

        var seenLines = await serve;
        var ranged = Uri.UnescapeDataString(seenLines[0]);
        Assert.Contains("start_time=2026-11-01T10:00:00.500000Z", ranged);
        Assert.Contains("end_time=2026-11-02T00:00:00.000000Z", ranged);
        Assert.DoesNotContain("start_time", seenLines[1]);
        Assert.DoesNotContain("end_time", seenLines[1]);
    }
}
