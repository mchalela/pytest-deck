"""Inner plugin, injected into subprocesses via ``-p pytest_deck._inner``.

P1: deliberately not a pytest11 entry point (auto-loading it would write JSON
onto an inherited fd in every pytest run on the machine). Emits one JSON line
per record on the dedicated fd (P4/P5): a ``collection`` line in collect mode,
and ``report``/``warning`` lines during a run.
"""

import json
import os


def _out_fd():
    """P5: the real fd number rides ``PYTEST_DECK_FD``; fall back to stdout."""
    raw = os.environ.get("PYTEST_DECK_FD")
    try:
        return int(raw) if raw is not None else 1
    except ValueError:
        return 1


def _write_all(fd, data):
    """os.write is one write(2) and may write fewer bytes than asked; loop."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _jsonable(value):
    """Best-effort JSON-safe copy: unknown types fall back to ``str()``.

    pytest-metadata's dict maps strings to strings in practice, but a
    hook-added value can be anything, and ``json.dumps`` raising inside
    ``_emit`` would lose the record.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _serialize_markers(item):
    # P8: iter_markers() captures inherited class/module marks, with args.
    return [
        {
            "name": mark.name,
            "args": [repr(a) for a in mark.args],
            "kwargs": {k: repr(v) for k, v in mark.kwargs.items()},
        }
        for mark in item.iter_markers()
    ]


def _render_longrepr(report):
    """Render ``report.longrepr`` exactly the way pytest's terminal does.

    Colours and pygments highlighting included. None when nothing to render.
    """
    longrepr = getattr(report, "longrepr", None)
    if longrepr is None:
        return None
    if isinstance(longrepr, str):
        return longrepr

    import io

    # [SPEC] _pytest._io is private API, but stable across pytest 7-9.
    from _pytest._io import TerminalWriter

    buf = io.StringIO()
    writer = TerminalWriter(file=buf)
    # Force markup on the StringIO-backed writer; pygments highlighting then
    # engages on its own (``code_highlight`` defaults to True). 16-colour only.
    writer.hasmarkup = True

    toterminal = getattr(longrepr, "toterminal", None)
    if toterminal is None:
        return str(longrepr)
    try:
        toterminal(writer)
    except Exception:  # never let rendering break the run
        return str(longrepr)
    return buf.getvalue().rstrip("\n")


class DeckInnerPlugin:
    """Stateful plugin instance holding the config and the output fd.

    Registered from the module-level ``pytest_configure`` (the idiomatic shape
    for a config-carrying plugin). It must not define a ``pytest_configure``
    method of its own: that is a historic hook, and pluggy would re-invoke it
    on the fresh instance at registration.
    """

    def __init__(self, config):
        """Store the config and resolve the output fd."""
        self.config = config
        self._fd = _out_fd()  # resolved once; the env is fixed before spawn

    def _emit(self, payload):
        line = (json.dumps(payload) + "\n").encode()
        try:
            _write_all(self._fd, line)
        except OSError:
            _write_all(1, line)  # last resort: better captured than lost

    def _emit_plugin_meta(self):
        """Emit one ``plugin_meta`` record carrying pytest-metadata's dict.

        Unlike ``mpl_name``, which goes out for every item because one short
        line is cheap, this record is gated: it is emitted when the plugin is
        actually loaded and has populated its stash, and stays silent
        otherwise, so this module keeps its dependency-free shape. The stable
        3.x API is
        ``config.stash[metadata_key]`` (``config._metadata`` does not exist);
        population happens in metadata's tryfirst ``pytest_configure``, so any
        post-collection read point is safe. Values are made JSON-safe with a
        ``str()`` fallback, since a non-serializable value would kill the emit
        (``json.dumps`` in ``_emit`` would raise).
        """
        try:
            from pytest_metadata.plugin import metadata_key
        except ImportError:
            return
        try:
            meta = self.config.stash.get(metadata_key, None)
            if not isinstance(meta, dict) or not meta:
                return
            self._emit({"$deck": "plugin_meta", "data": _jsonable(meta)})
        except Exception:
            return  # never let metadata reading break the run

    def pytest_collection_finish(self, session):
        """Emit the ``collection`` line, in collect mode only.

        P6: in run mode it is ignored downstream and its suite-scaled size
        blew the 1 MiB buffer. P7: read-only over ``session.items``.

        A run instead emits the small ``plugin_meta`` record here (a
        verified-safe read point, since pytest-metadata populates its stash in
        ``pytest_configure``); collect fd-3 stays minimal and the P6 gate on
        the ``collection`` line is untouched.
        """
        if not self.config.option.collectonly:
            self._emit_plugin_meta()
            return
        items = [
            {
                "nodeid": item.nodeid,
                "path": str(getattr(item, "path", "")),
                "name": item.name,
                "markers": _serialize_markers(item),
            }
            for item in session.items
        ]
        self._emit({"$deck": "collection", "items": items})

    def pytest_collectreport(self, report):
        """Emit a ``collect_error`` per failed collect node.

        Covers module and conftest import errors, rendered like run-mode
        tracebacks: the dashboard shows a partial tree plus errors, exactly
        like pytest's ERRORS section.
        """
        if not report.failed:
            return
        self._emit(
            {
                "$deck": "collect_error",
                "nodeid": report.nodeid,
                "path": str(getattr(report, "fspath", "") or report.nodeid),
                "longrepr_text": _render_longrepr(report),
            }
        )

    def pytest_runtest_logreport(self, report):
        """Emit a ``report`` line per phase (setup/call/teardown).

        P9: pytest's official serializer, plus the native terminal render
        as ``longrepr_text``.
        """
        data = self.config.hook.pytest_report_to_serializable(
            config=self.config, report=report
        )
        text = _render_longrepr(report)
        if text is not None:
            data["longrepr_text"] = text
        self._emit({"$deck": "report", "report": data})

    def pytest_runtest_setup(self, item):
        """Emit a ``mpl_name`` line joining a nodeid to mpl's dotted name.

        pytest-mpl keys its ``results.json`` by ``module.cls.name`` (its
        private ``generate_test_name``), not by the nodeid the deck tree uses.
        We reproduce that exact formula from the live ``item`` here, the one
        reliable source: going from a nodeid back to a module depends on import
        mode and rootdir, and is ambiguous on duplicate basenames.
        ``item.name`` already carries the ``[param]`` id, so parametrized cases
        join correctly. If mpl ever changes its formula the join test fails
        loudly rather than returning silently empty.

        Emitted for each and every item (cheap, one short line): the deck
        consults the map only for manifests declaring an mpl artifact
        transport, and gating on mpl being active would couple the inner plugin
        to mpl internals.
        """
        module = getattr(item, "module", None)
        module_name = getattr(module, "__name__", None)
        if module_name is None:
            return  # non-Python item (e.g. doctest); mpl doesn't touch it
        cls = getattr(item, "cls", None)
        if cls is not None:
            dotted = f"{module_name}.{cls.__name__}.{item.name}"
        else:
            dotted = f"{module_name}.{item.name}"
        self._emit({"$deck": "mpl_name", "nodeid": item.nodeid, "dotted": dotted})

    def pytest_warning_recorded(self, warning_message, when, nodeid):
        """Emit a ``warning`` line.

        Warnings never appear in a TestReport, so they ride separately,
        tagged with the nodeid that caused them.
        """
        category = getattr(warning_message.category, "__name__", None) or str(
            getattr(warning_message, "category", "")
        )
        self._emit(
            {
                "$deck": "warning",
                "nodeid": nodeid,
                "when": when,
                "category": category,
                "message": str(warning_message.message),
                "filename": getattr(warning_message, "filename", ""),
                "lineno": getattr(warning_message, "lineno", None),
            }
        )


def pytest_configure(config):
    """Register the ``DeckInnerPlugin`` instance on the inner session."""
    config.pluginmanager.register(DeckInnerPlugin(config), "pytest-deck-inner")
