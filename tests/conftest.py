"""Offline tests must not accidentally use a real service or consume API quota."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network_for_offline_tests(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Network access requires an explicit integration test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
