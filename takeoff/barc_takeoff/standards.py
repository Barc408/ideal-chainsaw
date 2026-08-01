"""BARC's own framing conventions, kept separate from what the drawings say.

The split matters. Anything an engineer specifies - species, grade, member
sizes, nailing, stud counts at headers - is read from the plan set and is not
negotiable. Everything in here is how BARC chooses to build and buy, applies to
every job, and is the part worth tuning once rather than per project.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "standards" / "barc-framing.yaml"


@dataclass
class Standards:
    data: dict[str, Any]
    path: Path

    @property
    def confirmed(self) -> bool:
        """Whether a human has signed off on these numbers.

        Seeded defaults are plausible but unverified, and a lumber order priced
        off unverified waste factors is a real cost. Reports say so until this
        flips.
        """
        return str(self.data.get("review_status", "")).upper() == "CONFIRMED"

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def waste(self, category: str) -> float:
        return float(self.get("waste", category, default=0.0))

    def stock_lengths(self, size: str) -> list[int]:
        return list(self.get("stock_lengths_ft", size, default=[]))


def load(path: str | Path | None = None) -> Standards:
    p = Path(path) if path else DEFAULT_PATH
    with open(p) as fh:
        return Standards(data=yaml.safe_load(fh) or {}, path=p)
