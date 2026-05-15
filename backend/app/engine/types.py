from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Orientation(str, Enum):
    H = "H"
    V = "V"
    OTHER = "OTHER"


@dataclass(slots=True)
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def expand(self, pad: float) -> "BBox":
        return BBox(self.x1 - pad, self.y1 - pad, self.x2 + pad, self.y2 + pad)

    def intersects(self, other: "BBox") -> bool:
        return not (self.x2 < other.x1 or self.x1 > other.x2 or self.y2 < other.y1 or self.y1 > other.y2)

    def contains_point(self, p: tuple[float, float]) -> bool:
        return self.x1 <= p[0] <= self.x2 and self.y1 <= p[1] <= self.y2


@dataclass(slots=True)
class EntityRecord:
    entity: Any
    dxftype: str
    layer: str
    bbox: BBox

    @property
    def center(self) -> tuple[float, float]:
        return self.bbox.center


@dataclass(slots=True)
class LineSegment:
    id: str
    orientation: Orientation
    x1: float
    y1: float
    x2: float
    y2: float
    length: float
    layer: str

    @property
    def axis(self) -> float:
        return (self.y1 + self.y2) / 2 if self.orientation == Orientation.H else (self.x1 + self.x2) / 2

    @property
    def a(self) -> float:
        return min(self.x1, self.x2) if self.orientation == Orientation.H else min(self.y1, self.y2)

    @property
    def b(self) -> float:
        return max(self.x1, self.x2) if self.orientation == Orientation.H else max(self.y1, self.y2)


@dataclass(slots=True)
class SemanticSpan:
    id: str
    orientation: Orientation
    a: float
    b: float
    axis: float
    length: float
    zone_id: str
    semantic_type: str
    priority: int
    source_line_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DimensionCandidate:
    id: str
    orientation: Orientation
    p1: tuple[float, float]
    p2: tuple[float, float]
    base: tuple[float, float]
    value: float
    zone_id: str
    semantic_type: str
    priority: int
