"""HTTPS for the tablet page.

Browsers give a page the microphone (and AudioWorklet, and a few other
things) only over HTTPS.  A local network address cannot have a certificate
from a public authority, so URDP makes its own once, with ``openssl``, and
keeps it; the tablet's browser warns about it the first time.

The web server's handlers stay plain: each TLS connection is unwrapped by one
thread that pumps bytes between it and one end of a socket pair, and the
handler gets the other end.  One thread per connection also keeps OpenSSL's
rule that a connection is used by one thread at a time, which a WebSocket
read in one thread and written in another would break.

A browser that comes over plain HTTP (an old bookmark) is sent on to HTTPS.
"""

from __future__ import annotations

import hashlib
import os
import select
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .store import config_dir

HANDSHAKE_TIMEOUT = 15
#: Seconds a tablet may leave what is sent untaken before it is dropped, and
#: how long what is left is still offered once the server side is done.
STALL, LINGER = 30, 10
_CHUNK = 256 * 1024


def _folder() -> Path:
    return config_dir() / "tablet-tls"


def _make(folder: Path) -> bool:
    """A new key and self-signed certificate in *folder*."""
    if not shutil.which("openssl"):
        return False
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=str(folder.parent), prefix=".tls-"))
    try:
        result = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec",
             "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
             "-days", "3650", "-subj", "/CN=URDP Second Screen",
             "-keyout", str(work / "key.pem"), "-out", str(work / "cert.pem")],
            capture_output=True, timeout=30)
        if result.returncode != 0 or not (work / "cert.pem").is_file():
            return False
        os.chmod(work / "key.pem", 0o600)
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(work, folder)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


def context() -> ssl.SSLContext | None:
    """The server's TLS context, making the certificate on first use; None
    when it cannot be had (then the page stays on plain HTTP)."""
    folder = _folder()
    for attempt in range(2):
        try:
            made = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            made.minimum_version = ssl.TLSVersion.TLSv1_2
            made.options |= ssl.OP_NO_RENEGOTIATION
            made.load_cert_chain(folder / "cert.pem", folder / "key.pem")
            return made
        except (OSError, ssl.SSLError):
            if attempt or not _make(folder):
                return None
    return None


def fingerprint() -> str:
    """SHA-256 of the certificate, as browsers show it, or ""."""
    try:
        pem = (_folder() / "cert.pem").read_text("ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, ValueError):
        return ""
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def is_tls(sock: socket.socket) -> bool:
    """Whether the client opened with a TLS handshake (it speaks first)."""
    sock.settimeout(HANDSHAKE_TIMEOUT)
    first = sock.recv(1, socket.MSG_PEEK)
    return first == b"\x16"


def unwrap(sock: socket.socket, made: ssl.SSLContext,
           on_end: Callable[[], None] | None = None) -> socket.socket:
    """Does the handshake on *sock* and returns a plain socket that carries
    the decrypted stream; a thread of its own pumps it until either side
    closes or the tablet stops taking what is sent, then calls *on_end*.
    Raises OSError when the handshake fails."""
    sock.settimeout(HANDSHAKE_TIMEOUT)
    tls = made.wrap_socket(sock, server_side=True)
    inner, outer = socket.socketpair()
    try:
        threading.Thread(target=_pump, args=(tls, outer, on_end),
                         daemon=True).start()
    except RuntimeError as exc:             # no thread to be had
        for item in (tls, inner, outer):
            item.close()
        raise OSError("no thread for the connection") from exc
    return inner


def _pump(tls: ssl.SSLSocket, plain: socket.socket,
          on_end: Callable[[], None] | None) -> None:
    try:
        _shuttle(tls, plain)
    finally:
        for sock in (plain, tls):
            try:
                sock.close()
            except OSError:
                pass
        if on_end is not None:
            on_end()


def _shuttle(tls: ssl.SSLSocket, plain: socket.socket) -> None:
    """Moves bytes both ways until done.  Idle is fine (a quiet WebSocket);
    what ends it early is a tablet that stops taking what is sent: after
    STALL seconds without progress, or LINGER seconds once the handler is
    done, the connection is dropped, so it cannot hold a thread forever."""
    tls.setblocking(False)
    plain.setblocking(False)
    to_plain = to_tls = b""
    tls_open = plain_open = True
    told = False                          # the handler heard the tablet end
    # What OpenSSL asked for: a send that needs a read first, or a recv
    # that needs a write (renegotiation is off, but TLS may still ask).
    send_wants_read = recv_wants_write = False
    progress = time.monotonic()
    poller = select.poll()
    while True:
        if not tls_open and not to_plain and plain_open and not told:
            told = True
            try:
                plain.shutdown(socket.SHUT_WR)        # the tablet is done
            except OSError:
                pass
        if not plain_open and not to_tls:
            return                                # the handler is done
        if not tls_open and not to_plain and not plain_open:
            return
        if to_tls:
            waited = time.monotonic() - progress
            if waited > (STALL if plain_open else LINGER):
                return                            # the tablet stopped reading
        tls_events = plain_events = 0
        if tls_open and not to_plain:
            tls_events |= select.POLLOUT if recv_wants_write else select.POLLIN
        if to_tls:
            tls_events |= select.POLLIN if send_wants_read else select.POLLOUT
        if plain_open and not to_tls:
            plain_events |= select.POLLIN
        if to_plain:
            plain_events |= select.POLLOUT
        if not tls_events and not plain_events:
            return
        # Decrypted bytes OpenSSL already holds do not wake poll.
        if not (tls_open and not to_plain and tls.pending()):
            for sock in (tls, plain):
                try:
                    poller.unregister(sock)
                except KeyError:
                    pass
            if tls_events:
                poller.register(tls, tls_events)
            if plain_events:
                poller.register(plain, plain_events)
            ready = dict(poller.poll(1000))
            if any(e & (select.POLLERR | select.POLLNVAL)
                   for e in ready.values()):
                return
            if not ready:
                continue                          # look at the clock again
        if tls_open and not to_plain:
            recv_wants_write = False
            try:
                data = tls.recv(_CHUNK)
                if data:
                    to_plain = data
                else:
                    tls_open = False
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLWantWriteError:
                recv_wants_write = True
            except (OSError, ssl.SSLError):
                tls_open = False
        if plain_open and not to_tls:
            try:
                data = plain.recv(_CHUNK)
                if data:
                    to_tls = data
                    progress = time.monotonic()
                else:
                    plain_open = False
            except BlockingIOError:
                pass
            except OSError:
                plain_open = False
        if to_plain:
            try:
                sent = plain.send(to_plain)
                to_plain = to_plain[sent:]
            except BlockingIOError:
                pass
            except OSError:
                return
        if to_tls:
            send_wants_read = False
            try:
                sent = tls.send(to_tls)
                to_tls = to_tls[sent:]
                if sent:
                    progress = time.monotonic()
            except ssl.SSLWantWriteError:
                pass
            except ssl.SSLWantReadError:
                send_wants_read = True
            except (OSError, ssl.SSLError):
                return
