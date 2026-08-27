"""Reshape serialized pytest reports into the per-phase wire shape.

The wire seam with ``_inner.py``: field names mirror pytest's TestReport
(``nodeid``/``when``/``outcome``/``duration``/``longrepr``/``sections``/
``wasxfail``).
"""


def reshape_report(run_id, report):
    """Reshape a serialized pytest report into the per-phase wire shape."""
    # Prefer the inner plugin's native render; otherwise reconstruct it.
    longrepr = report.get("longrepr_text")
    if longrepr is None:
        longrepr = _stringify_longrepr(report.get("longrepr"))
    return {
        "run_id": run_id,
        "nodeid": report.get("nodeid"),
        "when": report.get("when"),
        "outcome": report.get("outcome"),
        "duration": report.get("duration"),
        "longrepr": longrepr,
        "sections": _stringify_sections(report.get("sections")),
        # ``wasxfail`` distinguishes xfail/xpass from a plain skip/pass.
        "wasxfail": report.get("wasxfail"),
    }


def _stringify_longrepr(longrepr):
    """Render a serialized longrepr as a fallback.

    Used when the inner plugin's native ``longrepr_text`` is absent: it
    reconstructs the frames from ``reprtraceback``.
    """
    if longrepr is None:
        return None
    if isinstance(longrepr, str):
        return longrepr
    if not isinstance(longrepr, dict):
        return str(longrepr)

    lines = []
    reprtb = longrepr.get("reprtraceback")
    if isinstance(reprtb, dict):
        for entry in reprtb.get("reprentries") or []:
            data = entry.get("data") if isinstance(entry, dict) else None
            if not isinstance(data, dict):
                continue
            lines.extend(data.get("lines") or [])
            fileloc = data.get("reprfileloc")
            if isinstance(fileloc, dict):
                path = fileloc.get("path", "")
                lineno = fileloc.get("lineno", "")
                msg = fileloc.get("message", "")
                lines.append(f"{path}:{lineno}: {msg}")
            lines.append("")  # blank line between frames

    crash = longrepr.get("reprcrash")
    if isinstance(crash, dict) and crash.get("message"):
        if not lines:
            return crash["message"]
        lines.append(crash["message"])

    text = "\n".join(lines).rstrip()
    return text or str(longrepr)


def _stringify_sections(sections):
    """``sections`` is a list of ``[title, content]`` pairs of captured output."""
    if not sections:
        return []
    out = []
    for section in sections:
        if isinstance(section, (list, tuple)) and len(section) == 2:
            out.append({"title": str(section[0]), "content": str(section[1])})
    return out
