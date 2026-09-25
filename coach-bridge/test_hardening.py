"""Härtung der Bridge vor der Veröffentlichung (0.10 Task 28).

- Ein Authorization-Header mit Nicht-ASCII-Zeichen ist ein 401, keine Ausnahme im Handler.
- Eine Verbindung, die nichts schickt, belegt keinen Handler-Thread auf Dauer (Slowloris im LAN).
"""
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import bridge


class AuthorizationHeaderTests(unittest.TestCase):
    def test_non_ascii_or_surrogate_header_is_rejected_not_raised(self):
        with patch.object(bridge, "TOKEN", "secret"):
            for header in ("Bearer sécret", "Bearer secreÿ", "Bearer \udcff", "Bearer \ud800"):
                with self.subTest(header=ascii(header)):
                    self.assertEqual(bridge.authorization_status(header), 401)
            self.assertEqual(bridge.authorization_status("Bearer secret"), 200)


class RequestTimeoutTests(unittest.TestCase):
    def test_handler_has_a_finite_socket_timeout(self):
        self.assertIsNotNone(bridge.Handler.timeout)
        self.assertGreater(bridge.Handler.timeout, 0)
        # Länger als der SSE-Keepalive (15 s), damit ein ruhiger Stream nicht abreißt.
        self.assertGreater(bridge.Handler.timeout, 15)

    def test_idle_connection_is_closed_after_the_timeout(self):
        with patch.object(bridge.Handler, "timeout", 0.3):
            server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=5) as client:
                    client.sendall(b"POST /coach HTTP/1.1\r\n")  # Kopfzeilen kommen nie
                    started = time.monotonic()
                    self.assertEqual(client.recv(1024), b"")
                    self.assertLess(time.monotonic() - started, 4)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
