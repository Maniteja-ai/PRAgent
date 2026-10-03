"""Every foundation test is offline; a network attempt is an error."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    def guard(original):
        def blocked(sock, address, *args, **kwargs):
            # Windows asyncio uses a loopback socket pair for Playwright's event loop.
            if (
                request.node.get_closest_marker("browser")
                and isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1", "localhost"}
            ):
                return original(sock, address, *args, **kwargs)
            raise AssertionError("External network disabled in coordinator unit tests")

        return blocked

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
