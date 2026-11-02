"""Hard caps on paid API calls for an unattended run.

Set ``WK_BUDGET_FILE`` to a JSON file such as ``{"caps": {"sam": 25, "spark": 40}, "used": {}}``. Every real (not cached) SAM 3.1 or
Muse Spark request calls :func:`take` first; once a cap is reached it raises :class:`BudgetExceeded`, which the callers treat like a failed
call, so the stage stops with what it has. Without the variable nothing is counted or limited.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


def _file() -> Path | None:
    p = os.environ.get("WK_BUDGET_FILE")
    return Path(p) if p else None


def take(kind: str, what: str = "") -> None:
    f = _file()
    if f is None:
        return
    d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"caps": {}, "used": {}}
    used = d.setdefault("used", {}).get(kind, 0)
    cap = d.get("caps", {}).get(kind)
    if cap is not None and used >= cap:
        d.setdefault("refused", {})[kind] = d.get("refused", {}).get(kind, 0) + 1
        f.write_text(json.dumps(d, indent=1), encoding="utf-8")
        raise BudgetExceeded(f"{kind} call cap reached ({used}/{cap}); refused: {what[:80]}")
    d["used"][kind] = used + 1
    d.setdefault("log", []).append({"kind": kind, "what": what[:120]})
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")


def used(kind: str) -> int:
    f = _file()
    if f is None or not f.is_file():
        return 0
    return int(json.loads(f.read_text(encoding="utf-8")).get("used", {}).get(kind, 0))
