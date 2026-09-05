from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":   # KEY="value" / KEY='value'
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


@dataclass
class Config:
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    api_key: str = ""
    data_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        _load_dotenv(self.root / ".env")
        self.api_key = os.environ.get("OPENDOTA_API_KEY", "") or self.api_key
        d = os.environ.get("BP_DATA_DIR") or str(self.root / "data")
        self.data_dir = Path(d)
        for sub in ("raw", "db", "snapshots"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "bp.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"


CONFIG = Config()
