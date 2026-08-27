"""Enable the ``pytester`` fixture for the plugin test suite.

``pytester`` ships with pytest but must be explicitly enabled; it runs small
in-process (or subprocess) pytest sessions so we can assert on how our plugin
behaves when actually loaded.
"""

import tempfile

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _run_tmpdirs_under_pytest_basetemp(tmp_path_factory, monkeypatch):
    """Keep ``RunManager``'s per-run ``mkdtemp`` dirs out of the system /tmp.

    A run's tmpdir is cleaned by the NEXT run or by ``shutdown()``; tests that
    build a manager and never shut it down (most of them) would otherwise leave
    one ``pytest-deck-run-*`` dir behind per run. Pointing ``tempfile`` at
    pytest's basetemp puts them under its retention policy instead.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("runs")))
