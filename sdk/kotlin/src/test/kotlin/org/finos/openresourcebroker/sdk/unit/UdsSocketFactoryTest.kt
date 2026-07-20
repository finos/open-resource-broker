package org.finos.openresourcebroker.sdk.unit

import org.finos.openresourcebroker.sdk.transport.UdsSocketFactory
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class UdsSocketFactoryTest {

    @Test
    fun `UdsSocketFactory creates a socket`() {
        val factory = UdsSocketFactory("/tmp/nonexistent.sock")
        // createSocket() should return a socket without immediately connecting
        val sock = factory.createSocket()
        assertNotNull(sock)
        assertFalse(sock.isConnected)
        // Don't connect — just verify the factory is instantiable
    }

    @Test
    fun `UdsSocketFactory with different paths are independent`() {
        val f1 = UdsSocketFactory("/tmp/sock1.sock")
        val f2 = UdsSocketFactory("/tmp/sock2.sock")
        assertNotNull(f1.createSocket())
        assertNotNull(f2.createSocket())
    }
}
