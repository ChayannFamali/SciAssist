"""Smoke check for routes — list all paths."""
from sciassist.web.app import app


def walk(rs, prefix=""):
    for r in rs:
        p = getattr(r, "path", None)
        if p is None:
            continue
        m = sorted(getattr(r, "methods", [])) or ["MOUNT"]
        print(" ".join(m), prefix + p)
        inner = getattr(r, "routes", None)
        if inner:
            walk(inner, prefix + p)


walk(app.routes)