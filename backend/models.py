"""
models.py — SQLAlchemy model for face ROI data.

Stores axis-aligned minimal bounding boxes (top, right, bottom, left)
for each detected face, along with the timestamp of detection.
PostgreSQL is used because the data is structured, relational,
and benefits from ACID guarantees + efficient time-ordered queries.
"""

from datetime import datetime
from sqlalchemy import Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class ROI(Base):
    """
    A single face Region of Interest detected in one video frame.

    Bounding box convention (face_recognition / dlib):
        top    — y-coordinate of the top edge
        right  — x-coordinate of the right edge
        bottom — y-coordinate of the bottom edge
        left   — x-coordinate of the left edge

    This forms the axis-aligned minimal bounding box (AABB) of the face.
    """

    __tablename__ = "roi"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    box_top: Mapped[int] = mapped_column(Integer, nullable=False)
    box_right: Mapped[int] = mapped_column(Integer, nullable=False)
    box_bottom: Mapped[int] = mapped_column(Integer, nullable=False)
    box_left: Mapped[int] = mapped_column(Integer, nullable=False)

    @property
    def width(self) -> int:
        return self.box_right - self.box_left

    @property
    def height(self) -> int:
        return self.box_bottom - self.box_top

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.box_left + self.box_right) / 2

    @property
    def center_y(self) -> float:
        return (self.box_top + self.box_bottom) / 2

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "detected_at": self.detected_at.isoformat(),
            "bounding_box": {
                "top":    self.box_top,
                "right":  self.box_right,
                "bottom": self.box_bottom,
                "left":   self.box_left,
            },
            "dimensions": {
                "width":    self.width,
                "height":   self.height,
                "area":     self.area,
                "center_x": round(self.center_x, 1),
                "center_y": round(self.center_y, 1),
            },
        }
