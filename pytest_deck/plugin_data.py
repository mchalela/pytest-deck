"""First-party slimmers for plugin transport payloads.

A manifest with a ``[transport]`` section declares a post-run file the runner
reads after the child exits; the raw file can be huge (coverage JSON carries
per-line data), so only a slimmed shape rides the ``plugin_data`` SSE event.
Slimming is plugin-specific code keyed off the manifest id (P16): a transport
on an id with no registered slimmer is a manifest validation error, not a
pass-through.
"""

import json
import os

import pytest_deck

# Cap the generic render payload so a huge json/text artifact can't blow the
# fd-3/SSE budget (the same spirit as coverage slimming). Measured on the raw
# bytes read off disk. Over the cap, text is truncated (and flagged) and json is
# reported as too large.
RENDER_MAX_BYTES = 256 * 1024

# Cap the nesting depth of a render="json" payload too. Where the interpreter's
# recursion guard trips is a moving target (CPython 3.14 parses ~50k-deep
# documents that 3.13 refuses, and the C-stack guard depends on platform and
# stack state), and a document json.loads accepts here must also survive the
# server layer's json.dumps on every subscriber's SSE stack, where a
# RecursionError would kill the event stream (results are never dropped, B4).
# A fixed cap makes the degrade deterministic on every supported Python: deeper
# than this is treated like a malformed file and becomes plugin_empty. Real
# plugin reports nest a handful of levels; 500 is beyond any legitimate
# artifact and far below every recursion guard.
RENDER_MAX_DEPTH = 500


class SlimTooLarge:
    """A slimmer's over-cap degrade: the slimmed dict beat RENDER_MAX_BYTES.

    Distinct from the runner's raw-read cap (``_OVER_CAP``, a transport file
    over ``SLIM_MAX_BYTES``): here the plugin ran and saved everything, we just
    can't put the payload on the wire. The runner translates this into
    ``plugin_empty`` (P18's exactly-one-of unchanged) carrying the truthful
    ``reason`` so the frontend doesn't claim "no data" for a run that has it.
    """

    def __init__(self, reason):
        """Store the human-readable ``reason`` shipped on ``plugin_empty``."""
        self.reason = reason


# A big JSON artifact is almost always big because of one or two enormous
# embedded fields (raw per-sample or per-record arrays; coverage's per-line data
# is one shipped example). Over the cap we can't render it, but we can still
# tell the user which top-level keys exist so they know what's bloating it. Only
# this prefix is read to find them, never the whole file (a full parse of a
# 100+ MB artifact would OOM the runner thread). Top-level keys precede their
# giant values, so a small prefix is enough.
_KEY_SCAN_PREFIX = 64 * 1024
_KEY_SCAN_MAX = 64  # cap the reported key list


def _top_level_keys(prefix):
    """Read top-level object keys off a bounded prefix, best effort.

    Never raises. Scans a JSON prefix at brace-depth 1, collecting strings that
    are followed by ``:`` (object keys). Bounded work over ``prefix`` alone;
    returns ``[]`` on a non-object top, on garbage, or on a prefix that ends
    mid-key. Not a full parser, just enough to name what is big for the
    over-cap signal.
    """
    try:
        text = prefix.decode("utf-8", "replace")
    except Exception:
        return []
    start = text.find("{")
    if start < 0:
        return []  # top level isn't an object (array/scalar), so nothing to name
    keys = []
    depth = 0
    i = start
    n = len(text)
    while i < n and len(keys) < _KEY_SCAN_MAX:
        c = text[i]
        if c == '"' and depth == 1:
            try:
                s, end = json.decoder.scanstring(text, i + 1)
            except Exception:
                break  # the string runs past the prefix; stop cleanly
            j = end
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] == ":":
                keys.append(s)
                i = j + 1
                continue
            i = end
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return keys


def _max_depth(text):
    """Maximum bracket-nesting depth of a JSON document. Never raises.

    Iterative scan (no recursion): strings are skipped with the real JSON
    string scanner so brackets inside them don't count. Early-outs as soon as
    ``RENDER_MAX_DEPTH`` is exceeded. Garbage input yields whatever depth the
    scan sees; ``json.loads`` stays the real validity gate.
    """
    depth = max_depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            try:
                _, i = json.decoder.scanstring(text, i + 1)
            except Exception:
                break  # unterminated string; json.loads will reject the document
            continue
        if c in "{[":
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth > RENDER_MAX_DEPTH:
                    break
        elif c in "}]":
            depth -= 1
        i += 1
    return max_depth


def render_payload(render, path):
    """Read a generic render artifact off ``path`` for the ``plugin_data`` event.

    ``render`` is ``"json"`` or ``"text"``. Returns ``(data, truncated)`` where
    ``truncated`` flags a size-capped payload, or ``None`` when the file is
    absent, unreadable or unparseable. That includes a JSON document nested
    deeper than ``RENDER_MAX_DEPTH``, where parsing and SSE re-serialization
    would sit on the interpreter's recursion guard and vary by CPython version.
    The runner emits ``plugin_empty`` then, same as for a missing transport.
    Size is capped at ``RENDER_MAX_BYTES``:

    * ``text``: the file is read up to the cap; an over-long file yields the
      first ``RENDER_MAX_BYTES`` decoded (``truncated=True``).
    * ``json``: an over-cap file is not partially parsed (the capped read would
      be invalid JSON); instead ``data`` is a marker dict ``{"_truncated":
      True, "bytes": <true file size>, "keys": [top-level keys]}`` so the
      frontend can name *which* top-level field is bloating the payload, not
      just say "too large". ``bytes`` is the real file size
      (``os.path.getsize``), not the capped read.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read(RENDER_MAX_BYTES + 1)
    except OSError:
        return None
    truncated = len(raw) > RENDER_MAX_BYTES
    if render == "text":
        raw = raw[:RENDER_MAX_BYTES]
        return raw.decode("utf-8", "replace"), truncated
    if render == "json":
        if truncated:
            # Over the cap we don't parse: the capped read is invalid JSON and
            # the full file is huge anyway. Report the true size and the
            # top-level keys so the panel is actionable, not a dead end.
            try:
                size = os.path.getsize(path)
            except OSError:
                size = len(raw)
            marker = {
                "_truncated": True,
                "bytes": size,
                "keys": _top_level_keys(raw),
            }
            return marker, True
        try:
            text = raw.decode("utf-8")
            if _max_depth(text) > RENDER_MAX_DEPTH:
                return None
            return json.loads(text), False
        except Exception:
            # Contract: any malformed or unreadable artifact degrades to
            # plugin_empty (quietly, like a missing file). The depth gate above
            # keeps json.loads here, and json.dumps on the SSE path, away from
            # the interpreter's recursion guard; that is the load-bearing case
            # that must never escape and strand the run (SSE has no replay).
            return None
    return None


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"})


def _artifact_kind(rel_path):
    """``"image"`` for a known image extension, else ``"file"``."""
    ext = os.path.splitext(rel_path)[1].lower()
    return "image" if ext in _IMAGE_EXTS else "file"


def parse_mpl_index(raw):
    """Turn pytest-mpl's ``results.json`` into per-test artifact lists.

    Returns ``{dotted_name: [{name, rel_path, kind}]}``. ``raw`` is the parsed
    JSON: a dict keyed by mpl's dotted test name (``module.cls.name``, from its
    private ``generate_test_name``). Each entry may carry ``result_image`` /
    ``baseline_image`` / ``diff_image`` as POSIX paths relative to the results
    dir (the transport ``root``), or ``None`` for a field mpl didn't produce.
    Only the present files of interest become artifacts, and ``name`` is the
    field each came from (``result``/``baseline``/``diff``). Returns ``{}`` for
    a non-dict document, so a schema drift degrades to "no artifacts" rather
    than a crash (P18).
    """
    if not isinstance(raw, dict):
        return {}
    fields = (
        ("result", "result_image"),
        ("baseline", "baseline_image"),
        ("diff", "diff_image"),
    )
    out = {}
    for dotted, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        files = []
        for name, key in fields:
            rel = entry.get(key)
            if isinstance(rel, str) and rel:
                files.append(
                    {"name": name, "rel_path": rel, "kind": _artifact_kind(rel)}
                )
        if files:
            out[dotted] = files
    return out


# index_format -> parser(raw) -> {dotted_name: [artifact]}. First-party, closed set.
INDEX_PARSERS = {"mpl": parse_mpl_index}


# Self-pollution fix: the deck's own installed package dir, resolved once.
# cov.json file keys are coverage's abs_file() output (realpath'd), so realpath
# here makes editable installs and symlinks compare correctly. Path-prefix
# comparison only, never name matching: a user project with its own
# ``pytest_deck/`` dir must not false-positive.
_DECK_DIR = os.path.realpath(os.path.dirname(pytest_deck.__file__))


def _is_under(path, directory):
    """Return True when ``path`` equals ``directory`` or lies inside it."""
    return path == directory or path.startswith(directory + os.sep)


def _cov_total(summaries):
    """coverage.py's roll-up total over per-file ``summary`` dicts.

    Mirrors ``Numbers.pc_covered`` (coverage 7.x, ``results.py``): 100 *
    (n_executed + n_executed_branches) / (n_statements + n_branches), 100.0 on
    a zero denominator. The JSON summary keys map ``covered_lines`` =
    n_executed, ``covered_branches`` = n_executed_branches (``make_summary`` /
    ``make_branch_summary`` in ``jsonreport.py``); the branch keys are absent
    without ``--cov-branch``, which counts as 0.
    """
    covered = 0
    denominator = 0
    for summary in summaries:
        covered += summary.get("covered_lines", 0) + summary.get("covered_branches", 0)
        denominator += summary.get("num_statements", 0) + summary.get("num_branches", 0)
    if denominator > 0:
        return 100.0 * covered / denominator
    return 100.0


def _slim_pytest_cov(raw, rootdir):
    """Slim a coverage.py JSON report to a total plus per-file percentages.

    The wire shape is ``{"total": %, "files": {relpath: %}}``. The per-line
    detail stays in the raw file (the source gutter reads it from the run
    tmpdir); the wire carries the run total and the per-file percentages.

    Self-pollution: bare ``--cov`` measures everything imported, including the
    deck's own injected ``_inner`` (and the package ``__init__`` its import
    pulls in), files a terminal ``pytest --cov`` never sees. Entries resolving
    under the deck's installed package and outside rootdir are dropped; the
    under-rootdir carve-out keeps them when the deck is the user's genuine
    ``--cov`` target (dogfooding on this repo). User source outside rootdir
    stays, because terminal pytest-cov reports it. After any drop the total is
    recomputed from the survivors (never taken from cov.json's polluted
    ``totals``); dropping everything returns ``None``, which becomes
    ``plugin_empty`` and matches the "no data was collected" a terminal run
    would show.
    """
    totals = raw.get("totals") or {}
    total = totals.get("percent_covered")
    if total is None:
        return None
    rootdir_real = os.path.realpath(rootdir)
    files = {}
    kept_summaries = []
    dropped = False
    for path, info in (raw.get("files") or {}).items():
        summary = (info or {}).get("summary") or {}
        pct = summary.get("percent_covered")
        if pct is None:
            continue
        if os.path.isabs(path):
            resolved = os.path.realpath(path)
        else:
            resolved = os.path.realpath(os.path.join(rootdir, path))
        if _is_under(resolved, _DECK_DIR) and not _is_under(resolved, rootdir_real):
            dropped = True
            continue
        if os.path.isabs(path):
            # coverage keys are cwd-relative when run from rootdir (P12: cwd is
            # pinned there), but normalize absolute keys defensively.
            try:
                path = os.path.relpath(path, rootdir)
            except ValueError:
                pass
        kept_summaries.append(summary)
        files[path] = pct
    if dropped:
        if not files:
            return None
        total = _cov_total(kept_summaries)
    return {"total": total, "files": files}


# The per-test stats subset that rides the wire (~317 B/record measured).
# Everything else in pytest-benchmark's stats block (quartiles, outlier counts,
# total) stays in the raw save file.
_BENCH_STATS = (
    "min",
    "max",
    "mean",
    "stddev",
    "median",
    "iqr",
    "ops",
    "rounds",
    "iterations",
)


def _slim_benchmark(raw, rootdir):
    """Slim a pytest-benchmark save file into a summary plus per-test stats.

    The wire shape is ``{summary, tests: {nodeid: stats}}``. ``raw`` is the
    ``--benchmark-save`` file (the same schema as ``--benchmark-json`` without
    ``stats.data``, the raw round timings). Each record's ``fullname`` is the
    literal pytest nodeid (verified on 5.2.3: ``node._nodeid``,
    rootdir-relative regardless of cwd, across params, classes and duplicate
    basenames under importlib), so it is a direct key: no sanitizer, no
    inner-plugin join records. A benchmarked callable that raises writes no
    record at all, so absent nodeids are normal; a record without a numeric
    ``mean`` is schema drift and is skipped. Zero usable records returns
    ``None``, which becomes ``plugin_empty``. ``summary`` carries the count
    plus fastest and slowest by mean (the run-panel line); a slimmed dict over
    ``RENDER_MAX_BYTES`` (roughly 830 or more records at about 317 B each)
    returns ``SlimTooLarge`` with a truthful reason, like the metadata slimmer.
    """
    benchmarks = raw.get("benchmarks") if isinstance(raw, dict) else None
    if not isinstance(benchmarks, list):
        return None
    tests = {}
    for record in benchmarks:
        if not isinstance(record, dict):
            continue
        nodeid = record.get("fullname")
        stats = record.get("stats")
        if not (isinstance(nodeid, str) and nodeid and isinstance(stats, dict)):
            continue
        if not isinstance(stats.get("mean"), (int, float)):
            continue
        tests[nodeid] = {key: stats.get(key) for key in _BENCH_STATS}
    if not tests:
        return None
    fastest = min(tests, key=lambda n: tests[n]["mean"])
    slowest = max(tests, key=lambda n: tests[n]["mean"])
    out = {
        "summary": {
            "count": len(tests),
            "fastest": {"nodeid": fastest, "mean": tests[fastest]["mean"]},
            "slowest": {"nodeid": slowest, "mean": tests[slowest]["mean"]},
        },
        "tests": tests,
    }
    if len(json.dumps(out).encode("utf-8")) > RENDER_MAX_BYTES:
        # Not None: the suite ran and the save file has everything, so a plain
        # plugin_empty would render as "no benchmark fixtures ran", which is a
        # lie.
        return SlimTooLarge(
            f"benchmark output too large to render ({len(tests)} results)"
        )
    return out


def _meta_scalar(value):
    """Stringify a metadata value for the key/value rows; strings pass through."""
    return value if isinstance(value, str) else str(value)


def _slim_metadata(raw, rootdir):
    """Turn pytest-metadata's stash dict into rows for the run panel.

    ``raw`` is the inner plugin's ``plugin_meta`` record data (the dict behind
    ``config.stash[metadata_key]``, already JSON-round-tripped). Scalar values
    are stringified defensively (``--metadata`` via extra args can inject
    non-strings); nested dicts (``Packages``/``Plugins``) pass through for
    JsonTree rendering, their values stringified the same way. Size-capped at
    ``RENDER_MAX_BYTES``, where going over returns ``SlimTooLarge`` with a
    truthful reason, like the benchmark slimmer. The dict is around 400 B in
    practice, so the cap is pure defense, in the same spirit as the generic
    render cap.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    out = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            out[str(key)] = {str(k): _meta_scalar(v) for k, v in value.items()}
        else:
            out[str(key)] = _meta_scalar(value)
    if len(json.dumps(out).encode("utf-8")) > RENDER_MAX_BYTES:
        return SlimTooLarge(
            f"environment metadata too large to render ({len(out)} keys)"
        )
    return out


# Manifest id -> slimmer(raw, rootdir) -> wire dict | None | SlimTooLarge.
# First-party only.
SLIMMERS = {
    "pytest_cov": _slim_pytest_cov,
    "metadata": _slim_metadata,
    "benchmark": _slim_benchmark,
}

# The wire `render` for each slimmer-backed transport, per manifest id. It lives
# next to SLIMMERS so a new slimmer declares its render here, never as a literal
# in the runner (the render-map rule: no hardcoded "coverage").
SLIM_RENDERS = {
    "pytest_cov": "coverage",
    "metadata": "metadata",
    "benchmark": "benchmark",
}


def slim(plugin_id, raw, rootdir):
    """Slim raw transport data for the wire; ``None`` means emit no event.

    A malformed file degrades exactly like a missing one, quietly: the user
    may have disabled the plugin's output via extra args. A ``SlimTooLarge``
    return passes through: data existed but the slimmed dict beat the render
    cap, and the runner owes the user that reason.
    """
    slimmer = SLIMMERS.get(plugin_id)
    if slimmer is None:
        return None
    try:
        return slimmer(raw, rootdir)
    except Exception:
        return None
