"""Capture the documentation screenshots and GIFs from a live deck.

Every image in ``docs/_static/screenshots/`` comes out of this script, so a UI
change is a re-run away from fresh pictures. It starts ``pytest --deck
examples/`` on a free port, drives the page with Playwright (headless Chromium)
and saves what the README and the user guide embed. Nothing here is part of the
test suite or CI: run it by hand before a release.

Setup, once, inside the project virtualenv::

    pip install playwright
    playwright install chromium

Then, from the repository root::

    python docs/screenshots/capture.py              # every shot, PNG + GIF
    python docs/screenshots/capture.py --only hero  # one shot
    python docs/screenshots/capture.py --no-gif     # stills only (fast)
    python docs/screenshots/capture.py --headed     # watch it drive the page

GIFs need ``ffmpeg`` on PATH. The example plugins (pytest-cov, pytest-benchmark,
pytest-django, pytest-asyncio, pytest-mpl) must be installed for their shots.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "examples"
OUT = ROOT / "docs" / "_static" / "screenshots"

# One viewport for every shot, so the pictures line up on a docs page. The
# device scale factor doubles the resolution for crisp text; GIFs are scaled
# back down to GIF_WIDTH at encode time.
VIEWPORT = {"width": 1360, "height": 840}
SCALE = 2
GIF_FPS = 10
GIF_WIDTH = 1280
# The test run is the long middle of every recording; it plays at this speed
# while the lead-in and the ending stay at real time.
GIF_RUN_SPEED = 2.0

# Sidebar and detail-pane widths as fractions of the window (what the app
# itself persists), seeded before load so every shot shares one layout.
PANES = {"left": 0.17, "right": 0.34}

RUN_TIMEOUT_MS = 10 * 60 * 1000


# --------------------------------------------------------------------------
# server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def deck_server(suite=SUITE):
    """Run ``pytest --deck SUITE`` for the duration of the block, yield its URL."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "--deck",
            str(suite),
            "--deck-port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(url + "api/plugins", timeout=1).close()
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("deck server exited before it came up")
                if time.monotonic() > deadline:
                    raise SystemExit("deck server did not come up within 60 s")
                time.sleep(0.25)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------
# page driving. Selectors mirror the Svelte components; if one breaks, the
# component markup changed and this file is the second place to update.


class Deck:
    """Thin wrapper around a Playwright page showing the deck."""

    def __init__(self, page, url):
        self.page = page
        self.url = url

    # -- lifecycle

    def open(self):
        self.page.goto(self.url)
        self.page.wait_for_selector(".row.file", timeout=RUN_TIMEOUT_MS)
        self.wait_idle()

    def wait_idle(self):
        """Wait until no collect or run is in flight (the Collect button enables)."""
        self.page.wait_for_function(
            "() => { const b = document.querySelector('header button');"
            " return b && !b.disabled; }",
            timeout=RUN_TIMEOUT_MS,
        )

    def pause(self, ms):
        self.page.wait_for_timeout(ms)

    # -- sidebar

    def plugin(self, label):
        return self.page.locator(
            ".plugin", has=self.page.locator(".label", has_text=label)
        )

    def set_plugin(self, label, on=True):
        """Flip a plugin switch; a collect-scoped plugin triggers a re-collect."""
        box = self.plugin(label).locator(":scope > .row > input[type=checkbox]")
        if box.is_checked() != on:
            box.click()
            self.pause(300)
            self.wait_idle()

    def set_field(self, label, field, value):
        """Set a plugin config field (text or bool) by its visible labels."""
        plugin = self.plugin(label)
        caret = plugin.locator(":scope > .row > .caret")
        if "open" not in (caret.get_attribute("class") or ""):
            caret.click()
        row = plugin.locator(
            "label.field", has=self.page.locator(".fname", has_text=field)
        )
        box = row.locator("input")
        if isinstance(value, bool):
            box.set_checked(value)
        else:
            box.fill(str(value))

    def click_marker(self, name):
        self.page.locator(".chips .chip", has_text=name).first.click()

    # -- tree

    def toolbar(self, label):
        self.page.locator("main.treepane .toolbar button", has_text=label).click()

    def group_row(self, name):
        """A file, class or parametrize group row by its exact displayed name."""
        exact = re.compile(f"^{re.escape(name)}$")
        return self.page.locator(
            ".row.group", has=self.page.locator(":scope > .name", has_text=exact)
        ).first

    def set_group(self, name, on=True):
        """Tick or untick a whole group (file, class, parametrized test)."""
        self.group_row(name).locator("input[type=checkbox]").set_checked(on)

    def set_expanded(self, name, open=True):
        caret = self.group_row(name).locator(".caret")
        is_open = "open" in (caret.get_attribute("class") or "")
        if is_open != open:
            caret.click()

    def reset_scroll(self):
        """Tree to the top, sidebar back to its left edge.

        Playwright scrolls whatever it clicks or fills into view, which can
        leave the tree mid-list or the sidebar shifted sideways after a long
        field label.
        """
        self.page.evaluate(
            "document.querySelector('main.treepane').scrollTop = 0;"
            "document.querySelector('aside.sidebar').scrollLeft = 0;"
            "document.querySelector('aside.sidebar').scrollTop = 0;"
        )

    def leaf_row(self, nodeid_suffix):
        """A leaf row whose nodeid (the name's title attribute) ends in the suffix."""
        return self.page.locator(
            f".row:not(.group):has(.name[title$='{nodeid_suffix}'])"
        ).first

    def open_test(self, nodeid_suffix):
        """Click a test's status badge, pinning it in the detail pane."""
        self.leaf_row(nodeid_suffix).locator(".status").click()
        self.page.wait_for_selector(".detailpane .outcome")

    # -- run

    def run(self):
        self.page.locator("header button.primary").click()
        self.page.wait_for_function(
            "() => (document.querySelector('header .meta')?.textContent || '')"
            ".startsWith('run finished')",
            timeout=RUN_TIMEOUT_MS,
        )
        self.wait_idle()

    def open_coverage_file(self, path):
        self.page.locator(
            ".covrow", has=self.page.locator(".covpath", has_text=path)
        ).click()
        self.page.wait_for_selector(".covsrc .ln")

    def screenshot(self, path):
        self.page.screenshot(path=str(path))
        print(f"  wrote {path.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# shots. Each is a function taking a Deck and a `mark(name)` callback; marks
# record moments of the session so the GIF can be cut from the recording. The
# still is taken at the end of the same scripted session.


def clean_collect(d):
    """Django and asyncio tests only collect with their plugins switched on."""
    d.set_plugin("Django")
    d.set_plugin("Async tests")


def hero(d, mark):
    clean_collect(d)
    d.toolbar("select all")
    d.set_group("bench/test_perf.py", False)  # needs the benchmark fixture
    d.toolbar("collapse")
    d.set_expanded("test_outcomes.py")  # statuses stream into view
    d.reset_scroll()
    d.pause(800)
    mark("start")
    d.run()
    mark("ran")
    d.pause(800)
    d.set_expanded("test_deep_traceback.py")
    d.open_test("::test_normalize_bad_element_deep")
    d.reset_scroll()
    d.pause(2500)
    mark("end")


def coverage(d, mark):
    clean_collect(d)
    d.toolbar("select all")
    d.set_group("bench/test_perf.py", False)
    d.toolbar("collapse")
    d.set_expanded("test_deep_traceback.py")
    d.reset_scroll()
    d.pause(800)
    mark("start")
    d.set_plugin("Coverage")
    d.pause(800)
    d.run()
    mark("ran")
    d.pause(1500)
    d.screenshot(OUT / "coverage-panel.png")
    d.open_coverage_file("toy.py")
    d.pause(2500)
    mark("end")


def bench(d, mark):
    clean_collect(d)
    d.set_plugin("Benchmarks")
    d.set_field("Benchmarks", "Max time", "0.2")
    d.toolbar("clear")
    d.toolbar("collapse")
    d.set_group("bench/test_perf.py", True)
    d.set_expanded("bench/test_perf.py")
    d.set_expanded("test_fib")
    d.reset_scroll()
    d.pause(800)
    mark("start")
    d.run()
    mark("ran")
    d.pause(800)
    d.open_test("::test_join_words")
    d.reset_scroll()
    d.pause(2500)
    mark("end")


# Stills come out of every shot; `gif` adds a recording. `panes` overrides the
# sidebar / detail-pane fractions for one shot.
SHOTS = {
    "hero": {"run": hero, "gif": True},
    "coverage": {"run": coverage, "gif": True},
    "bench": {"run": bench, "gif": False, "panes": {"left": 0.23, "right": 0.30}},
}


# --------------------------------------------------------------------------
# recording to GIF
#
# Playwright's built-in video is lossy VP8: the compression noise makes every
# GIF frame differ everywhere, which costs megabytes. Instead the page streams
# lossless PNG frames over the DevTools protocol (only when something repaints)
# and ffmpeg assembles them with their real timestamps, so a frame that changes
# one badge costs one badge.


class Screencast:
    """Collect lossless frames from the page via ``Page.startScreencast``."""

    def __init__(self, page, folder):
        self.folder = folder
        self.frames = []  # (wall-clock seconds, path)
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._on_frame)

    def start(self):
        self.cdp.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1})

    def stop(self):
        self.cdp.send("Page.stopScreencast")

    def _on_frame(self, params):
        path = self.folder / f"{len(self.frames):05d}.png"
        path.write_bytes(base64.b64decode(params["data"]))
        self.frames.append((params["metadata"]["timestamp"], path))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})


def frames_to_gif(frames, dst, start, fast_from, ran, end, workdir):
    """Encode frames timestamped in [start, end] into a palette-optimised GIF.

    The run in progress (`fast_from` to `ran`) plays at GIF_RUN_SPEED; the
    lead-in and the ending at real time. Flat UI colors need no dithering,
    which also keeps files small.
    """

    def retime(t):
        t = min(max(t, start), end)
        lead = min(t, fast_from) - start
        fast_part = max(0.0, min(t, ran) - fast_from)
        slow_part = max(0.0, t - ran)
        return lead + fast_part / GIF_RUN_SPEED + slow_part

    # The frame showing at `start` is the last one captured before it.
    before = [f for f in frames if f[0] <= start]
    inside = [f for f in frames if start < f[0] <= end]
    seq = (before[-1:] or inside[:1]) + inside
    if not seq:
        raise SystemExit("screencast produced no frames")
    total = retime(end)
    lines = ["ffconcat version 1.0"]
    for i, (ts, path) in enumerate(seq):
        nxt = seq[i + 1][0] if i + 1 < len(seq) else end
        dur = max(0.0, retime(nxt) - retime(max(ts, start)))
        lines += [f"file '{path}'", f"duration {dur:.3f}"]
    # The concat demuxer applies the last duration only when a file follows it.
    lines += [f"file '{seq[-1][1]}'"]
    playlist = workdir / "frames.ffconcat"
    playlist.write_text("\n".join(lines) + "\n")

    # stats_mode=full weighs every frame equally: a colour that appears only
    # at the end (the coverage gutter's green) still earns a palette slot,
    # where the motion-weighted "diff" mode folded it into a nearby grey.
    filters = (
        f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,"
        "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];"
        "[b][p]paletteuse=dither=none:diff_mode=rectangle"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(playlist),
        "-t",
        f"{total:.3f}",
        "-vf",
        filters,
        "-loop",
        "0",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


def capture(name, url, pw, want_gif, headed):
    print(f"[{name}]")
    spec = SHOTS[name]
    shot = spec["run"]
    want_gif = want_gif and spec["gif"]
    panes = spec.get("panes", PANES)
    # Headless Chromium hides scrollbars by default; a real browser shows
    # them, so keep them in the pictures.
    browser = pw.chromium.launch(
        headless=not headed, ignore_default_args=["--hide-scrollbars"]
    )
    tmp = Path(tempfile.mkdtemp(prefix="deck-shot-"))
    try:
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
        context.add_init_script(
            "window.localStorage.setItem('pytest-deck.panes', "
            f"'{json.dumps(panes)}')"
        )
        page = context.new_page()
        marks = {}

        def mark(label):
            marks[label] = time.time()  # same clock as the screencast frames

        d = Deck(page, url)
        d.open()
        cast = None
        if want_gif:
            cast = Screencast(page, tmp)
            cast.start()
        shot(d, mark)
        d.screenshot(OUT / f"{name}.png")
        if cast:
            cast.stop()
            gif = OUT / f"{name}.gif"
            frames_to_gif(
                cast.frames,
                gif,
                start=marks["start"] - 1.0,
                fast_from=marks["start"],
                ran=marks["ran"],
                end=marks["end"],
                workdir=tmp,
            )
            print(
                f"  wrote {gif.relative_to(ROOT)} ({gif.stat().st_size // 1024} KiB,"
                f" {len(cast.frames)} frames captured)"
            )
        context.close()
    finally:
        browser.close()
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated shot names (default: all)")
    ap.add_argument("--no-gif", action="store_true", help="stills only")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--list", action="store_true", help="list shot names and exit")
    args = ap.parse_args(argv)
    if args.list:
        print("\n".join(SHOTS))
        return 0
    names = args.only.split(",") if args.only else list(SHOTS)
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        ap.error(f"unknown shot(s): {', '.join(unknown)}; see --list")
    want_gif = not args.no_gif
    if want_gif and not shutil.which("ffmpeg"):
        ap.error("ffmpeg not found on PATH (use --no-gif for stills only)")

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with deck_server() as url, sync_playwright() as pw:
        for name in names:
            capture(name, url, pw, want_gif, args.headed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
