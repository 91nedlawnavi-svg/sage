"""One cooperative persistence gate per data root, including other processes.

Deletion holds the gate from preview validation through recovery. Foreground
turns and background passes hold it from context capture through final save,
so work using pre-deletion context cannot write after deletion completes.
"""

from contextlib import contextmanager
import fcntl
from functools import wraps
import inspect
import os
from pathlib import Path
import threading


class RecoveryRequired(OSError):
    """A committed deletion must finish before data can be used again."""


_gates = {}
_registry_lock = threading.Lock()


def _state(root):
    root = Path(root).resolve()
    with _registry_lock:
        lock, local = _gates.setdefault(root, (threading.RLock(), threading.local()))
    return root, lock, local


@contextmanager
def activity_gate(root, *, exclusive=False):
    """Shared provider-work lease; deletion alone takes an exclusive lease."""
    root, _, local = _state(root)
    if getattr(local, "activity_depth", 0):
        if exclusive and not local.exclusive:
            raise RuntimeError("Deletion cannot run inside an active provider operation")
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".deletion.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        if (root / ".session-deletion").exists():
            if (root / ".session-deletion").is_symlink():
                raise RecoveryRequired("Deletion recovery directory must not be a symlink")
            # No caller has captured context yet. Upgrade before exposing data.
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            exclusive = True
        local.activity_depth = 1
        local.exclusive = exclusive
        if (root / ".session-deletion").exists():
            from deletion import recover_deletion
            try:
                with data_gate(root):
                    recover_deletion(root)
            except Exception as exc:
                raise RecoveryRequired("Deletion recovery is pending; local data access is paused.") from exc
        yield
    finally:
        local.activity_depth = 0
        os.close(descriptor)


@contextmanager
def data_gate(root):
    root, lock, local = _state(root)
    # Short read/write operations serialize, but provider calls only hold the
    # shared activity lease, so streamed turns do not block history reads.
    with activity_gate(root), lock:
        if getattr(local, "depth", 0):
            local.depth += 1
            try:
                yield
            finally:
                local.depth -= 1
            return
        root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(root / ".persistence.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            local.depth = 1
            yield
        finally:
            local.depth = 0
            os.close(descriptor)


def guarded(root_of, *, activity=False):
    """Keep a whole read/context/provider/save operation inside the gate."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            gate = activity_gate if activity else data_gate
            with gate(root_of(*args, **kwargs)):
                return function(*args, **kwargs)
        return call
    return decorate


def guarded_store(cls):
    """Guard instance methods; static parsers remain usable for staged files."""
    for name, value in list(vars(cls).items()):
        if name in {"__init__", "_sync_generation", "_legacy_map"}:
            continue
        def wrap(function):
            @wraps(function)
            def call(self, *args, **kwargs):
                with data_gate(self.data_root):
                    sync = getattr(self, "_sync_generation", None)
                    if sync is not None:
                        sync()
                    return function(self, *args, **kwargs)
            return call
        if inspect.isfunction(value):
            setattr(cls, name, wrap(value))
        elif isinstance(value, property):
            setattr(cls, name, property(wrap(value.fget)))
    return cls
