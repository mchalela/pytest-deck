"""Async run path: spawn pytest, stream fd-3 reports and pty console over SSE.

``RunManager`` owns at most one in-flight run (kill-and-restart) and fans events
out to per-subscriber buffers the SSE endpoint drains. Two channels per run:
fd-3 for structured JSON results, and a pty for console text, which is never
parsed for results. Event names: started/report/warning/console/plugin_data/
plugin_empty/finished/cancelled/error.
"""

import asyncio
import fcntl
import glob
import json
import logging
import os
import pty
import shutil
import signal
import struct
import tempfile
import termios
import time
from pathlib import Path

from pytest import ExitCode

from ._subprocess import base_argv, build_env
from .events import Event, Subscriber
from .import_paths import import_dirs, pythonpath_argv_dirs
from .plugin_data import (
    INDEX_PARSERS,
    RENDER_MAX_BYTES,
    SLIM_RENDERS,
    SlimTooLarge,
    render_payload,
    slim,
)
from .reports import reshape_report

logger = logging.getLogger(__name__)

# One longrepr_text line (a full traceback) can exceed the 64 KiB default.
_FD3_LIMIT = 2**20

# Defensive cap on first-party slimmer transport reads (the render-is-None
# json path). Deliberately not RENDER_MAX_BYTES (256 KiB): a large real
# cov.json must keep slimming, and this only stops a pathological artifact
# from OOMing the runner thread. Over the cap becomes plugin_empty with a
# `reason`.
SLIM_MAX_BYTES = 32 * 2**20

# Returned by _read_one_transport when the transport file blew SLIM_MAX_BYTES:
# still plugin_empty (P18's exactly-one-of is unchanged), but with a reason the
# frontend can show instead of the generic "no data" hint. (The sibling
# degrade, a slimmed dict over the render cap, is plugin_data.SlimTooLarge,
# which carries its own reason.)
_OVER_CAP = object()


def _human_bytes(n):
    """Format a byte count for a message: ``32 * 2**20`` becomes ``"32 MiB"``.

    A sub-MiB cap (the test-patched case) is formatted in KiB, or in bytes.
    """
    if n % 2**20 == 0:
        return f"{n // 2**20} MiB"
    if n % 1024 == 0:
        return f"{n // 1024} KiB"
    return f"{n} B"


def _over_cap_reason():
    """Return the ``plugin_empty`` reason for a ``SLIM_MAX_BYTES`` overrun.

    Derived from the module constant at call time, so the message can never
    lie about the effective cap (tests patch it smaller).
    """
    return f"output exceeded the {_human_bytes(SLIM_MAX_BYTES)} cap"


# Fixed pty size so pytest renders banners/summary at a stable width.
_PTY_COLS = 120
_PTY_ROWS = 40

# SIGTERM grace before SIGKILL on kill-and-restart / cancel.
_TERM_GRACE = 3.0

# join() grace before cancelling reader tasks that never saw EOF (a grandchild
# holding the pty/fd-3 write ends open past the child's exit).
_JOIN_GRACE = 5.0


def _trim_expr(expr):
    """Normalize a -k/-m expression.

    Strips it, and a blank expression becomes None so the flag is omitted
    entirely (B13: a whitespace -m silently deselects everything).
    """
    if expr is None:
        return None
    stripped = expr.strip()
    return stripped or None


def _set_winsize(fd, rows, cols):
    """Set the pty window size so pytest renders at a fixed width."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def _resolve_transport_path(path):
    """Resolve a transport ``path`` that may carry ``*`` glob segments.

    pytest-benchmark's save dir has a host-derived machine-id segment the
    manifest can't spell (``{tmpdir}/benchmarks/*/0001_deck.json``), so a
    substituted path containing ``*`` (any number of them) is glob-expanded:
    zero matches gives ``None``, which becomes ``plugin_empty``, and several
    matches give the lexicographically last one. A fresh per-run tmpdir
    normally yields exactly one match, so taking the last is a deterministic
    tie-break, and for counter-named files it is the newest. Only ``*``
    triggers expansion; a ``*``-free path passes through untouched even if it
    contains other glob metacharacters (``?``/``[``), and even when it points
    at a nonexistent file (the read degrades there). This is a curated-only
    surface, so there is no count enforcement: it degrades rather than crashes,
    and it never raises, since a glob error resolves as no match.
    """
    if "*" not in path:
        return path
    try:
        matches = sorted(glob.glob(path))
    except Exception:
        return None
    return matches[-1] if matches else None


# Yielded by _fd3_lines in place of a line that blew the buffer limit.
OVERRUN = object()


def _is_overrun(exc):
    """Return True if ``exc`` is StreamReader's limit-overrun ValueError.

    ``readline`` raises one of two messages on overrun:
      * "Separator is found, but chunk is longer than limit"
      * "Separator is not found, and chunk exceed the limit"
    Anything else is a real error we should not swallow.
    """
    msg = str(exc)
    return "chunk is longer than limit" in msg or "chunk exceed the limit" in msg


async def _recover_overrun(reader, exc):
    """Restore the reader to the start of the next line after an overrun.

    Two cases (CPython ``StreamReader.readuntil``):

    * **Separator found** ("chunk is longer than limit"): the over-long line
      *including* its newline has already been consumed and discarded from
      the buffer, so the next ``readline`` starts cleanly on the following
      line. Nothing to drain.
    * **Separator not found** ("chunk exceed the limit"): the partial data is
      still buffered (no newline seen yet). Drain forward until we pass the
      next newline so the following ``readline`` is aligned, tolerating
      repeated overruns while draining a very long line.
    """
    if "chunk is longer than limit" in str(exc):
        return  # already consumed through the separator
    # Drain up to and including the next newline without pulling past it:
    # readuntil's LimitOverrunError.consumed is the byte count before the
    # not-yet-seen separator, so drain exactly that many and retry.
    while True:
        try:
            await reader.readuntil(b"\n")
            return  # buffer now starts at the next line
        except asyncio.IncompleteReadError:
            return  # EOF before another newline; nothing left to align to
        except asyncio.LimitOverrunError as over:
            try:
                await reader.readexactly(over.consumed)
            except asyncio.IncompleteReadError:
                return  # EOF mid-drain
        except ValueError:
            return  # unrelated reader error; stop draining


async def _fd3_lines(reader):
    """Yield fd-3 lines; yield ``OVERRUN`` for a line that blew the limit.

    An overrun recovers to the next line instead of killing the reader (a
    dead reader drops every later report). A genuine reader failure or EOF
    ends the iteration.
    """
    while True:
        try:
            line = await reader.readline()
        except ValueError as exc:
            if not _is_overrun(exc):
                return  # a genuine reader failure
            await _recover_overrun(reader, exc)
            yield OVERRUN
            continue
        except asyncio.IncompleteReadError:
            return
        if not line:
            return  # EOF: all writers closed
        yield line


class _Run:
    """One in-flight pytest subprocess and its three reader/waiter tasks."""

    def __init__(
        self,
        run_id,
        manager,
        rootdir,
        nodeids,
        k,
        m,
        extra_argv=None,
        env_templates=None,
        tmpdir=None,
        transports=None,
    ):
        self.run_id = run_id
        self._manager = manager
        self.rootdir = Path(rootdir).resolve()
        self.nodeids = list(nodeids or [])
        # Trimmed once here; the trimmed values are echoed on `started` (B13).
        self.k = _trim_expr(k)
        self.m = _trim_expr(m)
        # P16: pre-compiled plugin/extra tokens (server-side compile_argv /
        # compile_extra_args); already a token list, appended verbatim.
        self.extra_argv = list(extra_argv or [])
        self.env_templates = dict(env_templates or {})
        self.tmpdir = tmpdir  # run-scoped temp dir, owned by RunManager
        # [{"plugin": id, "path": template}]: post-exit files to slim onto
        # `plugin_data` events (before `finished`).
        self.transports = list(transports or [])

        self.proc = None
        self.started_event = None  # the `started` Event, replayed to late subs
        self.started_at = None
        self._tasks = []
        self._fd3_task = None  # the fd-3 reader task; _wait drains it post-exit
        self._cancel_reason = None  # set when killed: "superseded" | "user"
        self._saw_report = False
        self._done = asyncio.Event()
        # mpl dotted name -> nodeid, built from the inner plugin's `mpl_name`
        # fd-3 lines during the run; the artifact_dir transport joins mpl's
        # results.json keys against it post-exit.
        self._mpl_names = {}
        # pytest-metadata's dict from the inner plugin's `plugin_meta` fd-3
        # record (stashed mid-run, like _mpl_names); the fd3 transport resolves
        # it post-exit. None means the record never arrived.
        self._plugin_meta = None

    @property
    def is_alive(self):
        """Return True while the spawned subprocess has not exited.

        This is the deck's single liveness predicate.
        """
        return self.proc is not None and self.proc.returncode is None

    def _argv(self):
        # P20: the sibling-import dirs (P12) reach the child as `-o pythonpath=`
        # (collection-time sys.path insert, no bootstrap shadowing), merged with
        # the user's ini pythonpath so the deck never clobbers it. build_env
        # keeps only the deck source-root on the PYTHONPATH env.
        pp_dirs = pythonpath_argv_dirs(
            self.rootdir, import_dirs(self.rootdir, self.nodeids)
        )
        argv = base_argv(self.rootdir, pythonpath_dirs=pp_dirs)
        argv += ["--color=yes"]  # pty is a tty, but ask explicitly
        # B13: single `-opt=value` tokens, since a value starting with `-` would
        # be eaten by argparse as a flag; blank-after-strip values are omitted
        # in __init__.
        if self.k:
            argv += [f"-k={self.k}"]
        if self.m:
            argv += [f"-m={self.m}"]
        # P16: plugin `-p <entry-point-name>` tokens and tier-2 extra args ride
        # here, after the base/selection flags and before the positional nodeids.
        if self.extra_argv:
            # `{tmpdir}` in a token (transport output flags) becomes the run
            # tmpdir. Literal replace, same discipline as the [env] hook.
            tmp = str(self.tmpdir or "")
            argv += [tok.replace("{tmpdir}", tmp) for tok in self.extra_argv]
            # P11: a plain `-p name` unblocks an earlier `-p no:name`, and the
            # last -p wins, so hostile extra args (`-p xdist`) would silently
            # kill the fd-3 transport. Re-assert the deck's blocks after any
            # user-controlled tokens so they always come last.
            argv += ["-p", "no:xdist", "-p", "no:cacheprovider"]
        argv += self.nodeids
        return argv

    async def start(self):
        """Spawn the subprocess and launch the three I/O tasks."""
        loop = asyncio.get_running_loop()

        # fd 3: a normal pipe for structured JSON.
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)

        # pty for stdin/stdout/stderr at a fixed width with color.
        master_fd, slave_fd = pty.openpty()
        _set_winsize(slave_fd, _PTY_ROWS, _PTY_COLS)

        argv = self._argv()
        # P20: the sibling-import dirs are on argv's `-o pythonpath=` (see
        # _argv); build_env keeps only the deck source-root on the PYTHONPATH env.
        env = build_env(write_fd)
        env["COLUMNS"] = str(_PTY_COLS)
        env["LINES"] = str(_PTY_ROWS)
        # Manifest [env] hook: literal `{tmpdir}` substitution (like {value},
        # never str.format), e.g. COVERAGE_FILE so enabling coverage doesn't
        # drop .coverage into the user's tree.
        for key, template in self.env_templates.items():
            env[key] = template.replace("{tmpdir}", str(self.tmpdir or ""))

        self.started_at = time.time()

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.rootdir),
                env=env,
                pass_fds=(write_fd,),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,  # own process group for clean group-kill
            )
        except Exception as exc:  # spawn failure: error event, clean up fds
            os.close(read_fd)
            os.close(write_fd)
            os.close(master_fd)
            os.close(slave_fd)
            self._emit(
                Event(
                    "error",
                    {
                        "run_id": self.run_id,
                        "message": f"failed to start pytest: {exc}",
                        "exit_code": None,
                    },
                )
            )
            self._done.set()
            return

        # B14: the parent's copies get closed here; otherwise EOF never reaches
        # the readers.
        os.close(write_fd)
        os.close(slave_fd)

        self.started_event = Event(
            "started",
            {
                "run_id": self.run_id,
                "nodeids": self.nodeids,
                "k": self.k,
                "m": self.m,
                "argv": argv,
                "ts": self.started_at,
            },
        )
        self._emit(self.started_event)

        self._fd3_task = loop.create_task(
            self._read_fd3(loop, read_fd), name=f"{self.run_id}:fd3"
        )
        self._tasks = [
            self._fd3_task,
            loop.create_task(
                self._read_pty(loop, master_fd), name=f"{self.run_id}:pty"
            ),
            loop.create_task(self._wait(), name=f"{self.run_id}:wait"),
        ]

    async def _read_fd3(self, loop, read_fd):
        """Read fd-3 via ``_fd3_lines``; dispatch report/warning events."""
        reader = asyncio.StreamReader(limit=_FD3_LIMIT)
        protocol = asyncio.StreamReaderProtocol(reader)
        pipe = os.fdopen(read_fd, "rb", 0)
        try:
            transport, _ = await loop.connect_read_pipe(lambda: protocol, pipe)
        except Exception:
            # connect_read_pipe failed, so the pipe object still owns read_fd;
            # close it here (the success path hands ownership to the transport).
            # Mirrors _read_pty's guard so the fd can't leak.
            pipe.close()
            return
        try:
            async for line in _fd3_lines(reader):
                if line is OVERRUN:
                    # An overrun is recovered, not fatal: the run keeps going
                    # and `finished` still arrives. `fatal: False` tells the
                    # frontend to surface the message without ending the run
                    # (every other `error` event is terminal).
                    self._emit(
                        Event(
                            "error",
                            {
                                "run_id": self.run_id,
                                "message": "a result line exceeded the 1 MiB buffer "
                                "and was dropped (truncated traceback)",
                                "exit_code": None,
                                "fatal": False,
                            },
                        )
                    )
                    continue
                self._dispatch_fd3(line)
        finally:
            transport.close()

    def _dispatch_fd3(self, line):
        """Parse a fd-3 line and forward report/warning events; never raises.

        P10: dispatch on the known kinds and silently skip the rest, including
        a stray collection line. One bad line cannot be allowed to kill the
        reader (an escaped exception ends ``_read_fd3`` and silently drops
        every later report), so the catch around the per-line parse and
        dispatch is deliberately broad, like every other read seam
        (``_read_one_transport``, ``render_payload``): ``json.loads`` raises
        beyond ``JSONDecodeError`` (``RecursionError`` on a deeply nested doc,
        ``UnicodeDecodeError`` on non-UTF-8 bytes), and a valid-JSON non-dict
        line would break the kind dispatch below. Skip the line, keep reading.
        """
        try:
            self._dispatch_fd3_obj(json.loads(line))
        except Exception:
            # Silent by design (one bad line is expected noise), but traceable:
            # a dispatch bug would otherwise be invisible. Debug level only.
            logger.debug("skipped undispatchable fd-3 line", exc_info=True)
            return

    def _dispatch_fd3_obj(self, obj):
        """Dispatch one parsed fd-3 object (see ``_dispatch_fd3`` for the guard)."""
        kind = obj.get("$deck")
        if kind == "report":
            report = obj.get("report")
            if not report:
                return
            self._saw_report = True
            self._emit(Event("report", reshape_report(self.run_id, report)))
        elif kind == "mpl_name":
            # Not an SSE event; a private nodeid-to-mpl-name join record the
            # artifact_dir transport consults post-exit.
            dotted = obj.get("dotted")
            nodeid = obj.get("nodeid")
            if dotted and nodeid:
                self._mpl_names[dotted] = nodeid
        elif kind == "plugin_meta":
            # Not an SSE event either; pytest-metadata's dict, stashed for
            # the fd3 transport to resolve post-exit (mirrors _mpl_names).
            data = obj.get("data")
            if isinstance(data, dict) and data:
                self._plugin_meta = data
        elif kind == "warning":
            self._emit(
                Event(
                    "warning",
                    {
                        "run_id": self.run_id,
                        "nodeid": obj.get("nodeid"),
                        "when": obj.get("when"),
                        "category": obj.get("category"),
                        "message": obj.get("message"),
                        "filename": obj.get("filename"),
                        "lineno": obj.get("lineno"),
                    },
                )
            )

    async def _read_pty(self, loop, master_fd):
        """Read pty master in chunks; emit ``console`` events until EIO/EOF."""
        reader = asyncio.StreamReader(limit=_FD3_LIMIT)
        protocol = asyncio.StreamReaderProtocol(reader)
        pipe = os.fdopen(master_fd, "rb", 0)
        try:
            transport, _ = await loop.connect_read_pipe(lambda: protocol, pipe)
        except Exception:
            pipe.close()
            return
        try:
            while True:
                try:
                    chunk = await reader.read(4096)
                except (OSError, ValueError):
                    # Linux raises OSError(EIO) on the master when the slave
                    # closes; treat it as EOF.
                    break
                if not chunk:
                    break
                self._emit(
                    Event(
                        "console",
                        {
                            "run_id": self.run_id,
                            "text": chunk.decode("utf-8", "replace"),
                        },
                    )
                )
        finally:
            transport.close()

    async def _wait(self):
        """Await the subprocess; emit finished / cancelled / error then settle."""
        try:
            code = await self.proc.wait()
        except Exception as exc:
            self._emit(
                Event(
                    "error",
                    {
                        "run_id": self.run_id,
                        "message": f"run failed: {exc}",
                        "exit_code": None,
                    },
                )
            )
            self._done.set()
            return

        duration = time.time() - self.started_at if self.started_at else None

        if self._cancel_reason is not None:
            # A cancelled run resolves no transports and emits `cancelled` only;
            # return before the drain below so cancel latency never grows.
            self._emit(
                Event(
                    "cancelled",
                    {
                        "run_id": self.run_id,
                        "reason": self._cancel_reason,
                    },
                )
            )
            self._done.set()
            return

        # Drain the fd-3 reader to EOF before consulting anything it feeds
        # (`_saw_report`, the `_plugin_meta`/`_mpl_names` stashes): this waiter
        # and the reader task race after exit, and a lagging reader would make
        # a record that did arrive resolve as a spurious `plugin_empty` (P18
        # says exactly one of the two; this makes it the right one). The child
        # has exited, so its write end is closed and EOF is guaranteed; the wait
        # is still bounded and never raises (see _drain_fd3), mirroring join()'s
        # unconditional-but-bounded discipline.
        await self._drain_fd3()

        if code == ExitCode.USAGE_ERROR and not self._saw_report:
            # B12: exit 4 means an invalid -k/-m or a stale nodeid (the detail is
            # only on the pty console). NO_TESTS_COLLECTED (5) is benign and
            # falls through to `finished` so the UI shows "0 matched", never an
            # error.
            self._emit(
                Event(
                    "error",
                    {
                        "run_id": self.run_id,
                        "message": "invalid filter expression (-k/-m) or selected "
                        "tests not found. Check the console for pytest's error",
                        "exit_code": code,
                    },
                )
            )
        else:
            # Declared transport files are read post-exit, before the terminal
            # `finished`. Each declared transport yields exactly one event:
            # `plugin_data` (file present and the slimmer returned data) or
            # `plugin_empty` (declared but no usable data: an absent file, e.g.
            # --no-cov via extras, or coverage's "no data collected" JSON).
            if self.transports:
                # Defense in depth: a run that exits must always emit `finished`
                # (the load-bearing invariant; SSE has no replay, so a `_wait`
                # that emits nothing strands the run forever). Reading transport
                # files must never be able to prevent that, so any unexpected
                # error is swallowed here and control always falls through to
                # `finished`. (`_read_transports` already degrades a bad
                # artifact to plugin_empty; this catches anything it doesn't.)
                try:
                    results = await asyncio.to_thread(self._read_transports)
                except Exception:
                    results = []
                for name, payload in results:
                    self._emit(Event(name, payload))
            self._emit(
                Event(
                    "finished",
                    {
                        "run_id": self.run_id,
                        "exit_code": code,
                        "duration": duration,
                    },
                )
            )
        self._done.set()

    async def _drain_fd3(self):
        """Await the fd-3 reader task to EOF. Bounded, never raises.

        Called by ``_wait`` between process exit and transport resolution, so
        every buffered fd-3 line (late reports, the ``plugin_meta`` and
        ``mpl_name`` stash records) is dispatched before ``_read_transports``
        consults the stashes. It is bounded by ``_JOIN_GRACE``, because a
        grandchild holding the fd-3 write end open past the child's exit must
        not stall the terminal event (P18: a run that exits always emits
        ``finished``), and ``asyncio.wait`` never re-raises the task's
        exception, so an errored reader can't block it either. A no-op when the
        reader was never started (a spawn failure, or a unit-built run), and
        immediate when it already finished.
        """
        task = self._fd3_task
        if task is None:
            return
        await asyncio.wait({task}, timeout=_JOIN_GRACE)

    def _read_transports(self):
        """Read declared transport files; return ``[(event_name, payload)]``.

        One tuple per declared transport (sync, runs in a thread). With data
        present the tuple is ``("plugin_data", {run_id, plugin, render, data,
        ...})``; declared but with no usable data (an absent or unparseable
        file, or a slimmer that returned None) makes it ``("plugin_empty",
        {run_id, plugin})``. Never both, never neither.

        The ``render`` discriminator tells the frontend how to display
        ``data``: a first-party slimmed shape (``"coverage"``, ``"benchmark"``
        or ``"metadata"``, looked up per id in ``SLIM_RENDERS``, never a
        literal), ``"json"`` (a parsed JSON value), or ``"text"`` (a string). A
        generic render may also carry ``truncated: true`` when the artifact
        exceeded the size cap. Two over-cap degrades land as ``plugin_empty``
        carrying a ``reason``: a raw read over ``SLIM_MAX_BYTES``, and a
        slimmed dict over the render cap (``SlimTooLarge``). In that second
        case the plugin ran and saved everything, so the reason says "too
        large" rather than "no data".
        """
        tmp = str(self.tmpdir or "")
        events = []
        for transport in self.transports:
            plugin = transport["plugin"]
            render = transport.get("render")
            if transport.get("type") == "fd3":
                # No file; resolve from the record stashed off fd-3 mid-run.
                payload = self._read_fd3_transport(plugin)
            elif render == "artifacts":
                # artifact_dir: read the plugin's index and join it to nodeids.
                root = transport["root"].replace("{tmpdir}", tmp)
                payload = self._read_artifact_transport(
                    plugin, root, transport["index"], transport["index_format"]
                )
            else:
                path = _resolve_transport_path(
                    transport["path"].replace("{tmpdir}", tmp)
                )
                if path is None:  # glob matched nothing: no output this run
                    payload = None
                else:
                    payload = self._read_one_transport(plugin, render, path)
            if payload is _OVER_CAP:
                # The read-size cap takes the same reason-carrying degrade shape
                # as a slimmer's render-size cap; one translation below serves
                # both.
                payload = SlimTooLarge(_over_cap_reason())
            if isinstance(payload, SlimTooLarge):
                events.append(
                    (
                        "plugin_empty",
                        {
                            "run_id": self.run_id,
                            "plugin": plugin,
                            "reason": payload.reason,
                        },
                    )
                )
            elif payload is None:
                events.append(
                    ("plugin_empty", {"run_id": self.run_id, "plugin": plugin})
                )
            else:
                events.append(("plugin_data", payload))
        return events

    def _read_fd3_transport(self, plugin):
        """Resolve an ``fd3`` transport from the run's stashed record.

        The inner plugin emitted one ``plugin_meta`` record mid-run
        (pytest-metadata's stash dict) and ``_dispatch_fd3`` stashed it, so no
        file is read. Returns ``None``, which becomes ``plugin_empty``, when
        the record never arrived (the plugin was enabled but absent or silent)
        or when the slimmer rejects it. The wire ``render`` comes from the
        per-id ``SLIM_RENDERS`` map.
        """
        if self._plugin_meta is None:
            return None
        data = slim(plugin, self._plugin_meta, str(self.rootdir))
        if isinstance(data, SlimTooLarge):
            return data  # plugin_empty with the slimmer's reason
        if data is None:
            return None
        return {
            "run_id": self.run_id,
            "plugin": plugin,
            # Per-id first-party render map ("metadata"), never a literal.
            "render": SLIM_RENDERS.get(plugin),
            "data": data,
        }

    def _read_one_transport(self, plugin, render, path):
        """Read one transport file into a ``plugin_data`` payload, or ``None``.

        ``None`` means "declared but no usable data", which becomes
        ``plugin_empty``; ``_OVER_CAP`` means the file blew ``SLIM_MAX_BYTES``,
        and a ``SlimTooLarge`` means the slimmed dict blew the render cap. Both
        of those become ``plugin_empty`` with a ``reason``.
        """
        base = {"run_id": self.run_id, "plugin": plugin}
        if render is None:
            # First-party slimmer keyed by manifest id; the wire `render`
            # comes from the per-id SLIM_RENDERS map, never a literal.
            try:
                with open(path, "rb") as fh:
                    # Cap the read before parsing; an unbounded json.load of a
                    # pathological artifact would OOM the runner thread.
                    raw_bytes = fh.read(SLIM_MAX_BYTES + 1)
            except OSError:
                return None
            if len(raw_bytes) > SLIM_MAX_BYTES:
                return _OVER_CAP
            try:
                raw = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                # Unparseable: the switch was on, the output isn't. `Exception`
                # (not just ValueError) so a RecursionError from a deeply nested
                # cov.json degrades to plugin_empty instead of crashing.
                return None
            data = slim(plugin, raw, str(self.rootdir))
            if isinstance(data, SlimTooLarge):
                return data  # plugin_empty with the slimmer's reason
            if data is None:
                return None
            return {**base, "render": SLIM_RENDERS.get(plugin), "data": data}
        # Generic pass-through: parsed JSON or raw text, size-capped.
        result = render_payload(render, path)
        if result is None:
            return None
        data, truncated = result
        return {**base, "render": render, "data": data, "truncated": truncated}

    def _read_artifact_transport(self, plugin, root, index, index_format):
        """Read an ``artifact_dir`` index and join it to nodeids.

        Reads ``{root}/{index}`` (e.g. mpl's ``results.json``), parses it with
        the first-party ``index_format`` parser into ``{dotted_name: [files]}``,
        then joins each dotted name to a nodeid via the inner plugin's live map
        (``_mpl_names``), producing a ``plugin_data`` payload. Its ``render``
        is ``"artifacts"`` and its ``data`` maps each nodeid to
        ``[{name, rel_path, kind}]`` (rel_path relative to ``root``, the HTTP
        surface's serve base). Returns ``None``, which becomes
        ``plugin_empty``, when the index is absent or unparseable or nothing
        joins: a green mpl run writes no failures, and a run where mpl wasn't
        actually active writes no index.
        """
        parser = INDEX_PARSERS.get(index_format)
        if parser is None:
            return None
        index_path = Path(root) / index
        try:
            with open(index_path, "rb") as fh:
                # Cap the index read before json.loads (like the generic
                # render path in plugin_data.render_payload). A pathological
                # index, huge or deeply nested, must degrade to plugin_empty
                # and still let `_wait` emit `finished` (P18), never blow the
                # runner-thread budget or OOM. Over the cap we refuse
                # (plugin_empty).
                data_bytes = fh.read(RENDER_MAX_BYTES + 1)
            if len(data_bytes) > RENDER_MAX_BYTES:
                return None
            raw = json.loads(data_bytes.decode("utf-8"))
        except Exception:
            # Absent or unparseable index (incl. RecursionError on a nested doc):
            # the switch was on, the output isn't. Degrade quietly like every
            # transport.
            return None
        try:
            by_dotted = parser(raw)
        except Exception:
            return None
        data = {}
        for dotted, files in by_dotted.items():
            nodeid = self._mpl_names.get(dotted)
            if nodeid is not None:
                data[nodeid] = files
        if not data:
            return None
        return {
            "run_id": self.run_id,
            "plugin": plugin,
            "render": "artifacts",
            "data": data,
        }

    def _emit(self, event):
        self._manager.broadcast(event)

    async def kill(self, reason):
        """Kill the process group (SIGTERM, a grace period, then SIGKILL).

        Also records ``reason`` as the run's cancel reason.
        """
        self._cancel_reason = reason
        if not self.is_alive:
            return
        proc = self.proc
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERM_GRACE)
        except asyncio.TimeoutError:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    async def join(self):
        """Await the three tasks so no fds, tasks or zombies leak (§5).

        Bounded: readers finish on EOF, which the child's exit plus our closed
        write copies normally guarantee, but a grandchild that inherited the
        pty or fd-3 write ends can hold EOF back forever. After the grace we
        cancel the stragglers rather than hang the next run's start.
        """
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=_JOIN_GRACE)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        try:
            # `_done` is set by the _wait task; if that was just cancelled
            # above it may never fire, so don't hang on it.
            await asyncio.wait_for(self._done.wait(), timeout=_JOIN_GRACE)
        except asyncio.TimeoutError:
            pass


class RunManager:
    """Owns the single in-flight run and fans events out to SSE subscribers."""

    def __init__(self, rootdir):
        """Create a manager rooted at ``rootdir``."""
        self.rootdir = Path(rootdir).resolve()
        self._run = None  # the current/last _Run
        self._tmpdir = None  # the current/last run's temp dir
        self._run_counter = 0
        self._subscribers = set()  # set[Subscriber]
        self._lock = asyncio.Lock()

    # --- subscription / fan-out ------------------------------------------

    def subscribe(self):
        """Register a new subscriber.

        B9: it re-emits ``started`` only while the run is live; the stream
        carries live events, with no replay.
        """
        sub = Subscriber()
        self._subscribers.add(sub)
        run = self._run
        if run is not None and run.started_event is not None and run.is_alive:
            sub.put(run.started_event)
        return sub

    def unsubscribe(self, sub):
        """Drop ``sub`` from the fan-out set and close its buffer."""
        self._subscribers.discard(sub)
        sub.close()

    def broadcast(self, event):
        """Fan an event out to every subscriber.

        The backpressure split lives in ``Subscriber.put`` (INVARIANTS B4).
        """
        for sub in self._subscribers:
            sub.put(event)

    def is_active(self):
        """Return True iff a run's subprocess is currently alive.

        B9: polled via GET /api/run/active by the reconnect resync.
        """
        run = self._run
        return run is not None and run.is_alive

    # --- run lifecycle ---------------------------------------------------

    async def start(
        self,
        nodeids,
        k=None,
        m=None,
        extra_argv=None,
        env_templates=None,
        transports=None,
    ):
        """Kill any in-flight run, then spawn a new one. Returns the run_id.

        ``extra_argv`` and ``env_templates`` carry the server-compiled plugin
        and extra-args tokens plus the manifest env (P16); ``transports``
        carries the post-run files to slim onto ``plugin_data`` events.
        """
        async with self._lock:
            await self._kill_current("superseded")

            self._run_counter += 1
            run_id = f"run-{self._run_counter}"
            # The run tmpdir lives until the next run starts, not until this run
            # ends: the runner reads post-run report files (coverage JSON) from it.
            self._cleanup_tmpdir()
            self._tmpdir = tempfile.mkdtemp(prefix=f"pytest-deck-{run_id}-")
            run = _Run(
                run_id,
                self,
                self.rootdir,
                nodeids,
                k,
                m,
                extra_argv=extra_argv,
                env_templates=env_templates,
                tmpdir=self._tmpdir,
                transports=transports,
            )
            self._run = run
            await run.start()
            return run_id

    async def cancel(self):
        """Cancel the in-flight run. Returns ``(cancelled, run_id)``."""
        async with self._lock:
            run = self._run
            if run is None or not run.is_alive:
                return False, (run.run_id if run else None)
            await run.kill("user")
            await run.join()
            return True, run.run_id

    async def _kill_current(self, reason):
        run = self._run
        if run is None:
            return
        if run.is_alive:
            await run.kill(reason)
        # Join unconditionally: even a proc that already exited can have reader
        # tasks still draining buffered fd-3/pty lines, and without this, events
        # tagged with the old run_id can land after the new run's `started`.
        await run.join()

    def _cleanup_tmpdir(self):
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def coverage_file(self, run_id):
        """Return ``(cov_json_path, rootdir)`` for ``run_id``, or ``None``.

        The cov.json lives in the last run's tmpdir (it survives only until the
        next run starts). Returns ``None`` when the id isn't the last run, when
        the tmpdir or the file is gone, or when that run had no coverage, and
        the endpoint maps that to a clean 404, never a 500.
        """
        run = self._run
        if run is None or run.run_id != run_id or self._tmpdir is None:
            return None
        cov = Path(self._tmpdir) / "cov.json"
        if not cov.is_file():
            return None
        return cov, run.rootdir

    def artifact_root(self, run_id):
        """Return ``(root, rootdir)`` for ``run_id``'s artifact_dir, or ``None``.

        ``root`` is the resolved directory the artifact transport served files
        from (the run tmpdir's artifacts dir); ``rootdir`` is the run's project
        root. The HTTP artifact endpoint uses ``root`` as the two-gate realpath
        containment base and answers a clean 404 on ``None``, which happens
        when the id isn't the last run, when its tmpdir is gone, when the run
        declared no artifact_dir transport, or when the plugin wrote nothing
        (the dir never materialized). The rel_path from the ``plugin_data``
        payload resolves under ``root``.

        Security (artifact_dir gate 2, the serve-time half): even a curated
        transport's resolved ``root`` has to resolve under the run tmpdir. A
        ``root`` that escapes (a curated-code bug, or a template that somehow
        dodged the parse-time ``{tmpdir}`` requirement) returns ``None`` so the
        endpoint 404s rather than serving an out-of-tmpdir base. Belt and
        suspenders with the parse gate; mirrors user_manifests containment.
        """
        run = self._run
        if run is None or run.run_id != run_id or self._tmpdir is None:
            return None
        tmp = str(self._tmpdir)
        tmproot = Path(tmp).resolve()
        for transport in run.transports:
            if transport.get("render") == "artifacts":
                root = Path(transport["root"].replace("{tmpdir}", tmp)).resolve()
                try:
                    root.relative_to(tmproot)  # escaped tmpdir: refuse to serve
                except ValueError:
                    return None
                if root.is_dir():
                    return root, run.rootdir
        return None

    async def shutdown(self):
        """Tear down any in-flight run cleanly (on server stop)."""
        async with self._lock:
            await self._kill_current("user")
            self._cleanup_tmpdir()
