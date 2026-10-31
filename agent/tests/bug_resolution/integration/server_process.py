"""Run the real CLI in an isolated child with no external network access."""

import socket
import sys
from typing import Any

from langgraph_cli.cli import cli


def main() -> None:
    """Keep the actual server/runtime while blocking unrelated external telemetry."""

    assert sys.version_info[:2] == (3, 14)
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def check(address: Any) -> None:
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            raise OSError("External network is disabled in server tests")

    def local_connect(sock: socket.socket, address: Any) -> None:
        check(address)
        return connect(sock, address)

    def local_connect_ex(sock: socket.socket, address: Any) -> int:
        check(address)
        return connect_ex(sock, address)

    def local_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> list:
        if host not in (None, "localhost", "127.0.0.1", "::1", b"127.0.0.1", b"::1"):
            raise OSError("External DNS is disabled in server tests")
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex
    socket.getaddrinfo = local_getaddrinfo
    cli()


if __name__ == "__main__":
    main()
