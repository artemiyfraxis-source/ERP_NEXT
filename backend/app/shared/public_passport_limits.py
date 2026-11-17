"""Passport admission shared by API workers in one container, without proxy/IP assumptions.

Only short-lived counters and hashed link identifiers are stored in the private runtime directory.
OS locks coordinate threads/processes and release PDF slots automatically when a worker exits.
"""

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class PassportLimitsUnavailable(Exception):
    """Shared admission state could not be accessed safely; do not bypass the limits."""


class PublicPassportLimits:
    def __init__(self, directory=None, *, views=60, failures=120, window_seconds=60, pdf_slots=2):
        self.directory = Path(directory) if directory is not None else Path(tempfile.gettempdir()) / "erp-public-passport-limits"
        self.views, self.failures, self.window_seconds, self.pdf_slots = views, failures, window_seconds, pdf_slots

    @contextmanager
    def _lock(self, name, *, wait=True):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle = (self.directory / name).open("a+b")
        except OSError as error:
            raise PassportLimitsUnavailable from error
        acquired = False
        try:
            try:
                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
            except OSError as error:
                raise PassportLimitsUnavailable from error
            deadline = time.monotonic() + 2
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise PassportLimitsUnavailable from error
                    if not wait:
                        break
                    if time.monotonic() >= deadline:
                        raise PassportLimitsUnavailable("Passport admission lock timeout") from error
                    time.sleep(.01)
            yield acquired
        finally:
            try:
                if acquired:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as error:
                raise PassportLimitsUnavailable from error
            finally:
                try:
                    handle.close()
                except OSError as error:
                    raise PassportLimitsUnavailable from error

    def _change(self, key, limit, *, now=None, refund_started=None):
        with self._lock("counters.lock"):
            now = time.monotonic() if now is None else now
            temporary = None
            try:
                path = self.directory / "counters.json"
                state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                # A clock reset (host reboot) invalidates windows as well as ordinary expiry.
                state = {key: value for key, value in state.items() if 0 <= now - value[0] < self.window_seconds}
                started, count = state.get(key, [now, 0])
                if refund_started is not None:
                    if started != refund_started or not count:
                        return None
                    count -= 1
                elif count >= limit:
                    return None
                else:
                    count += 1
                state[key] = [started, count]
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                                 prefix=".counters-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(state, output, separators=(",", ":"))
                os.replace(temporary, path)
                return started
            except (OSError, ValueError, TypeError, AttributeError, IndexError) as error:
                raise PassportLimitsUnavailable from error
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as error:
                        raise PassportLimitsUnavailable from error

    def allow_failure(self, *, now=None):
        return self._change("failures", self.failures, now=now) is not None

    @contextmanager
    def view(self, instrument_id, token, *, now=None):
        key = hashlib.sha256(f"{instrument_id}:{token}".encode()).hexdigest()
        started = self._change(key, self.views, now=now)
        try:
            yield started is not None
        except BaseException:
            if started is not None:
                self._change(key, self.views, now=now, refund_started=started)
            raise

    @contextmanager
    def pdf_slot(self):
        for index in range(self.pdf_slots):
            with self._lock(f"pdf-{index}.lock", wait=False) as acquired:
                if acquired:
                    yield True
                    return
        yield False
