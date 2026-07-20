/**
 * Layer 2: UNIX Domain Socket Transport
 *
 * OkHttp SocketFactory that dials a UNIX domain socket instead of TCP.
 * Compatible with Java 16+ UnixDomainSocketAddress (java.nio.channels).
 *
 * Usage:
 *   OkHttpClient.Builder()
 *       .socketFactory(UdsSocketFactory("/tmp/orb.sock"))
 *       .build()
 *
 * The HTTP Host header / URL host component is ignored — all connections
 * go through the socket file at socketPath.
 *
 * Implementation note: SocketChannel.open() by default uses TCP.
 * For UNIX domain sockets we must use SocketChannel.open(StandardProtocolFamily.UNIX).
 * The UdsChannelSocket wraps this channel and adapts it to the java.net.Socket interface.
 */

package org.finos.openresourcebroker.sdk.transport

import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.StandardProtocolFamily
import java.net.UnixDomainSocketAddress
import java.nio.ByteBuffer
import java.nio.channels.SocketChannel
import javax.net.SocketFactory

/**
 * SocketFactory that routes all connections through a UNIX domain socket.
 */
class UdsSocketFactory(private val socketPath: String) : SocketFactory() {

    override fun createSocket(): Socket = UdsChannelSocket(socketPath)

    override fun createSocket(host: String, port: Int): Socket =
        createSocket().also { it.connect(InetSocketAddress(host, port)) }

    override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int): Socket =
        createSocket().also { it.connect(InetSocketAddress(host, port)) }

    override fun createSocket(host: InetAddress, port: Int): Socket =
        createSocket().also { it.connect(InetSocketAddress(host, port)) }

    override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int): Socket =
        createSocket().also { it.connect(InetSocketAddress(address, port)) }
}

/**
 * A [Socket] adapter over a UNIX domain [SocketChannel].
 *
 * When OkHttp calls connect(InetSocketAddress), we ignore the TCP address
 * and connect to the configured UDS path instead.
 */
private class UdsChannelSocket(private val socketPath: String) : Socket() {
    // MUST use StandardProtocolFamily.UNIX — not the default TCP family
    private val channel: SocketChannel = SocketChannel.open(StandardProtocolFamily.UNIX)
    private var connected = false

    override fun connect(endpoint: java.net.SocketAddress?) = connect(endpoint, 0)

    override fun connect(endpoint: java.net.SocketAddress?, timeoutMillis: Int) {
        val udsAddr = UnixDomainSocketAddress.of(socketPath)
        channel.connect(udsAddr)
        connected = true
    }

    override fun getInputStream(): InputStream = ChannelInputStream(channel)
    override fun getOutputStream(): OutputStream = ChannelOutputStream(channel)
    override fun isConnected(): Boolean = connected && channel.isConnected
    override fun isClosed(): Boolean = !channel.isOpen
    override fun close() {
        connected = false
        channel.close()
    }
    override fun shutdownInput() { channel.shutdownInput() }
    override fun shutdownOutput() { channel.shutdownOutput() }
    override fun setSoTimeout(timeout: Int) { /* UDS doesn't support socket timeout via setSoTimeout */ }
    override fun getSoTimeout(): Int = 0
    override fun setTcpNoDelay(on: Boolean) { /* not applicable for UDS */ }
    override fun getTcpNoDelay(): Boolean = false
    override fun setKeepAlive(on: Boolean) { /* not applicable for UDS */ }
    override fun getKeepAlive(): Boolean = false
    override fun setReuseAddress(on: Boolean) { /* not applicable for UDS */ }
    override fun getReuseAddress(): Boolean = false
    override fun setSoLinger(on: Boolean, linger: Int) { /* not applicable for UDS */ }
    override fun getSoLinger(): Int = -1
    override fun getInetAddress(): InetAddress? = null
    override fun getLocalAddress(): InetAddress = InetAddress.getLoopbackAddress()
    override fun getPort(): Int = 0
    override fun getLocalPort(): Int = 0
    override fun getRemoteSocketAddress() = null
    override fun getLocalSocketAddress() = null
}

/**
 * InputStream backed by a NIO SocketChannel (blocking mode).
 */
private class ChannelInputStream(private val channel: SocketChannel) : InputStream() {
    private val buf = ByteBuffer.allocate(65536)

    override fun read(): Int {
        buf.clear().limit(1)
        val n = channel.read(buf)
        return if (n == -1) -1 else (buf.get(0).toInt() and 0xFF)
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (len == 0) return 0
        buf.clear()
        buf.limit(minOf(len, buf.capacity()))
        val n = channel.read(buf)
        if (n == -1) return -1
        buf.flip()
        buf.get(b, off, n)
        return n
    }
}

/**
 * OutputStream backed by a NIO SocketChannel (blocking mode).
 */
private class ChannelOutputStream(private val channel: SocketChannel) : OutputStream() {
    override fun write(b: Int) {
        channel.write(ByteBuffer.wrap(byteArrayOf(b.toByte())))
    }

    override fun write(b: ByteArray, off: Int, len: Int) {
        channel.write(ByteBuffer.wrap(b, off, len))
    }

    override fun flush() { /* NIO channels are not buffered */ }
}
