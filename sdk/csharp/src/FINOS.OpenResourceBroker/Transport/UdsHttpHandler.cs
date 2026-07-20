// Layer 2: UNIX Domain Socket Transport
//
// Uses SocketsHttpHandler.ConnectCallback (.NET 5+) to dial a UNIX socket.
// The HTTP host/port in the URL is ignored; all traffic goes to socketPath.
//
// This is the idiomatic .NET 8 approach — no third-party dependency.

using System.Net.Sockets;

namespace FINOS.OpenResourceBroker.Transport;

/// <summary>
/// Creates an <see cref="HttpMessageHandler"/> that routes all requests through
/// a UNIX domain socket at <paramref name="socketPath"/>.
/// </summary>
public static class UdsHttpHandlerFactory
{
    /// <summary>
    /// Build a <see cref="SocketsHttpHandler"/> that connects to <paramref name="socketPath"/>
    /// regardless of the HTTP host/port in the request URL.
    /// </summary>
    public static SocketsHttpHandler Create(string socketPath)
    {
        var handler = new SocketsHttpHandler
        {
            PooledConnectionIdleTimeout = TimeSpan.FromMinutes(5),
            PooledConnectionLifetime = TimeSpan.FromMinutes(10),
            ConnectCallback = async (context, ct) =>
            {
                var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
                try
                {
                    var endpoint = new UnixDomainSocketEndPoint(socketPath);
                    await socket.ConnectAsync(endpoint, ct).ConfigureAwait(false);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            }
        };
        return handler;
    }
}
