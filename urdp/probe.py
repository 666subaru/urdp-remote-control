"""Tell the user *why* a connection failed.

FreeRDP reports ``ERRCONNECT_CONNECT_FAILED`` for every kind of unreachable
host: wrong name, machine asleep, firewall, a stale route.  A plain TCP probe
separates those cases, which is usually the difference between "fix it in ten
seconds" and "stare at a log".
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from .i18n import _


@dataclass(frozen=True)
class Result:
    reachable: bool
    message: str
    addresses: tuple[str, ...] = ()


def check(host: str, port: int = 3389, timeout: float = 3.0) -> Result:
    """Try a TCP connection to *host*:*port* and describe what happened."""
    host = (host or "").strip()
    if not host:
        return Result(False, _("The computer name is empty."))

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return Result(False, _("\"{host}\" could not be resolved. The name may "
                               "be wrong, or DNS does not know it."
                               ).format(host=host))
    except OSError as error:
        return Result(False, _("Address could not be resolved: {error}"
                           ).format(error=error))

    addresses = tuple(dict.fromkeys(info[4][0] for info in infos))
    shown = ", ".join(addresses)
    last: Exception | None = None

    for family, kind, proto, _canon, address in infos:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(timeout)
        started = time.monotonic()
        try:
            sock.connect(address)
        except Exception as error:                    # noqa: BLE001
            last = error
            continue
        else:
            elapsed = (time.monotonic() - started) * 1000
            return Result(True,
                          _("{address}:{port} is open ({ms:.0f} ms). Nothing "
                            "wrong on the network side."
                            ).format(address=address[0], port=port,
                                     ms=elapsed),
                          addresses)
        finally:
            sock.close()

    if isinstance(last, ConnectionRefusedError):
        return Result(False,
                      _("{host} answers but port {port} is closed. Is Remote "
                        "Desktop enabled on Windows?"
                        ).format(host=shown, port=port), addresses)
    if isinstance(last, (socket.timeout, TimeoutError)):
        return Result(False,
                      _("Port {port} on {host} did not answer within {seconds:.0f} "
                        "seconds. The machine may be off or asleep, a firewall "
                        "may be blocking it, or the route to this address "
                        "(VPN / subnet route) may be broken."
                        ).format(port=port, host=shown, seconds=timeout),
                      addresses)
    return Result(False, _("Could not connect to {host}: {error}"
                       ).format(host=shown, error=last), addresses)
