from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RecordingStats:
    file: str
    seconds: float
    frames: int
    expected: int
    gaps: int

    @classmethod
    def measure(cls, file: str, elapsed: float, frames: int, fps: int, gaps: int) -> RecordingStats:
        return cls(file=file, seconds=round(elapsed, 1), frames=frames,
                   expected=int(elapsed * fps), gaps=gaps)

    def as_dict(self) -> dict:
        return asdict(self)


def write_log(path: str, stats: RecordingStats,
              gap_list: list[tuple[int, float]], gap_ms: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"경과       : {stats.seconds} s\n")
        f.write(f"기대 프레임: {stats.expected}\n")
        f.write(f"실제 프레임: {stats.frames}\n")
        f.write(f"결손 구간  : {stats.gaps}건 (기준 {gap_ms} ms)\n")
        for idx, d in gap_list:
            f.write(f"  frame {idx} 직후 {d} ms\n")
