import hashlib
import logging
import math
import pathlib
from dataclasses import dataclass

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib import patheffects
from shapely.geometry import Point, box

from total_history.maps import (CORNERS, DPI, FIGURE_SIZE, LABEL_SHADE, LINE_WIDTH, WORLD_PROJECTION, Color, MapRenderer,
                                Scene, corner_box, crowding, frame_outline, label_point, layouts, rendered, save,
                                setting_digest, shade)
from total_history.sources.cliopatria import Cliopatria

SIDE_FILLS = ("#e2b088", "#9db0d6")
BATTLE_RADIUS_DEGREES = 7
BATTLE_MARK_SIZE = 220
LABEL_SIZES = (11, 8.5)
LABEL_INSIDE = 0.55
LIKE_KILOMETRES = 500
LIKE_YEARS = 50
EARTH_RADIUS_KM = 6371

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConflictMapJob:
    """One conflict's map: the polities of its first year, each side's deck civilizations as Cliopatria names them, and
    the battlefield."""

    title: str
    year: int
    polities: gpd.GeoDataFrame
    sides: tuple[tuple[str, ...], tuple[str, ...]]
    point: tuple[float, float]
    output: pathlib.Path


class ConflictMapRenderer(MapRenderer):
    def render_conflict(self, job: ConflictMapJob) -> None:
        """The battlefield and the land around it, only the two sides painted, each in its colour and named in white,
        the battlefield crossed, the year in a corner and both sides on the world inset."""
        point = Point(job.point)
        fills = {name: SIDE_FILLS[index] for index, side in enumerate(job.sides) for name in side}
        fighting = job.polities[job.polities.Name.isin(fills)]
        scene = Scene(self, "", job.polities, point.buffer(BATTLE_RADIUS_DEGREES), fighting.geometry.union_all(), [])
        figure, axes = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
        figure.subplots_adjust(0, 0, 1, 1)
        figure.patch.set_facecolor(Color.OCEAN)
        axes.set_axis_off()
        gpd.GeoSeries([scene.land], crs=scene.crs).plot(ax=axes, color=Color.LAND, linewidth=0)
        painted = scene.others[scene.others.Name.isin(fills)]
        painted.plot(ax=axes, color=[fills[name] for name in painted.Name], linewidth=0)
        gpd.GeoSeries([scene.lines], crs=scene.crs).plot(ax=axes, color=Color.LINE, linewidth=LINE_WIDTH, zorder=3)
        left, bottom, right, top = scene.frame.bounds
        axes.set_xlim(left, right)
        axes.set_ylim(bottom, top)
        spot = gpd.GeoSeries([point], crs="EPSG:4326").to_crs(scene.crs)
        axes.scatter(spot.x, spot.y, marker="X", s=BATTLE_MARK_SIZE, color=Color.HALO, edgecolors=Color.TEXT,
                     linewidths=1.4, zorder=7)
        corners = sorted(CORNERS, key=lambda corner: crowding(scene, corner))
        placed = [self._sides_inset(figure, scene, job, corners[0]), self._caption(figure, axes, job.year, corners[1])]
        for name in fills:
            self._side_label(figure, axes, scene, name, fills[name], placed)
        save(rendered(figure), job.output)

    def _sides_inset(self, figure, scene: Scene, job: ConflictMapJob, corner):
        """The world map of the civilization maps, with each side in its colour."""
        x0, y0, x1, y1 = corner_box(scene, corner).bounds
        left, bottom, right, top = scene.frame.bounds
        inset = figure.add_axes([(x0 - left) / (right - left), (y0 - bottom) / (top - bottom),
                                 (x1 - x0) / (right - left), (y1 - y0) / (top - bottom)])
        inset.set_facecolor(Color.INSET_BACKGROUND)
        inset.set_xticks([])
        inset.set_yticks([])
        for spine in inset.spines.values():
            spine.set_edgecolor(Color.LINE)
            spine.set_linewidth(0.6)
        self.world.plot(ax=inset, color=Color.INSET_LAND, linewidth=0)
        for index, side in enumerate(job.sides):
            shapes = job.polities[job.polities.Name.isin(side)]
            if not shapes.empty:
                shapes.to_crs(WORLD_PROJECTION).plot(ax=inset, color=SIDE_FILLS[index], linewidth=0, zorder=2)
        outline = gpd.GeoSeries([frame_outline(scene)], crs="EPSG:4326").to_crs(WORLD_PROJECTION)
        outline.plot(ax=inset, color=Color.LINE, linewidth=1, zorder=3)
        world_left, world_bottom, world_right, world_top = self.world.total_bounds
        inset.set_xlim(world_left, world_right)
        inset.set_ylim(world_bottom, world_top)
        inset.set_aspect("equal", adjustable="datalim")
        inset.set_xlabel("")
        inset.set_ylabel("")
        return inset.get_window_extent(figure.canvas.get_renderer())

    def _side_label(self, figure, axes, scene: Scene, name: str, fill: str, placed: list) -> None:
        """The side's name in white outlined in its colour's dark shade, inside one of its larger parts in view; left
        off when it fits in none."""
        shape = scene.shapes.get(name)
        if shape is None or shape.intersection(scene.frame).is_empty:
            return
        shown = shape.intersection(scene.frame)
        parts = sorted(getattr(shown, "geoms", [shown]), key=lambda part: part.area, reverse=True)[:3]
        renderer, to_data = figure.canvas.get_renderer(), axes.transData.inverted()
        outline = shade(fill, LABEL_SHADE)
        for size in LABEL_SIZES:
            for layout in layouts(name):
                for part in parts:
                    at = label_point(part)
                    text = axes.text(at.x, at.y, layout, fontsize=size, family=self.font, weight="semibold",
                                     color=Color.HALO, ha="center", va="center", linespacing=1.1, zorder=6,
                                     path_effects=[patheffects.withStroke(linewidth=2.5, foreground=outline)])
                    extent = text.get_window_extent(renderer)
                    (ax0, ay0), (ax1, ay1) = to_data.transform([(extent.x0, extent.y0), (extent.x1, extent.y1)])
                    area = box(ax0, ay0, ax1, ay1)
                    if (area.intersection(shape).area / area.area >= LABEL_INSIDE
                            and axes.bbox.contains(extent.x0, extent.y0) and axes.bbox.contains(extent.x1, extent.y1)
                            and not any(extent.overlaps(other) for other in placed)):
                        placed.append(extent)
                        return
                    text.remove()


def kilometres(first: tuple[float, float], second: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = (map(math.radians, first)), (map(math.radians, second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def map_jobs(conflicts: list[dict], civilizations: list[dict], cliopatria: Cliopatria,
             output: pathlib.Path) -> list[tuple[dict, ConflictMapJob]]:
    """A map for each conflict that is not a big war, has a battlefield, and has two sides each with a deck civilization
    Cliopatria draws in its first year within reach of the battlefield, so both are in view; a civilization's parts are
    drawn as it."""
    names = {document["id"]: (document["name"], document.get("parts", [])) for document in civilizations}
    jobs = []
    for conflict in conflicts:
        sides = conflict["sides"]
        if "big" in conflict["roles"] or not conflict["point"] or conflict["years"][0] is None or len(sides) < 2:
            continue
        year = conflict["years"][0]
        polities = cliopatria.at(year)
        renamed = {part: names[civilization][0] for side in sides[:2] for civilization in side["civilizations"]
                   for part in names[civilization][1]}
        polities = polities.assign(Name=polities.Name.replace(renamed))
        near = polities[polities.intersects(Point(conflict["point"]).buffer(BATTLE_RADIUS_DEGREES))]
        drawn = set(near.Name)
        pair = tuple(tuple(sorted(names[civilization][0] for civilization in side["civilizations"]
                                  if names[civilization][0] in drawn)) for side in sides[:2])
        if not all(pair):
            continue
        jobs.append((conflict, ConflictMapJob(conflict["title"], year, polities, pair, tuple(conflict["point"]),
                                              output / f"{conflict['slug']}.png")))
    return jobs


def told_apart(jobs: list[tuple[dict, ConflictMapJob]]) -> set[str]:
    """The conflicts whose map no other map could be taken for: none with the same two sides within 500 km and
    50 years of it."""
    alike = set()
    for (first, one), (second, other) in ((a, b) for index, a in enumerate(jobs) for b in jobs[index + 1:]):
        if set(one.sides) == set(other.sides) and abs(one.year - other.year) <= LIKE_YEARS \
                and kilometres(one.point, other.point) <= LIKE_KILOMETRES:
            alike |= {first["id"], second["id"]}
    return {conflict["id"] for conflict, _ in jobs} - alike


def fingerprint(job: ConflictMapJob, setting: str) -> str:
    shapes = job.polities.sort_values("Name")
    content = hashlib.sha256(setting.encode())
    content.update(repr((job.title, job.year, job.sides, job.point)).encode())
    content.update(shapes.drop(columns=shapes.geometry.name).to_csv(index=False).encode())
    for geometry in shapes.geometry:
        content.update(geometry.wkb if geometry is not None else b"")
    return content.hexdigest()


def draw_maps(jobs: list[ConflictMapJob], countries: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame, font: pathlib.Path,
              cache: pathlib.Path) -> None:
    """Draws each map, or copies it from the cache when drawn before from the same inputs and code."""
    cache.mkdir(parents=True, exist_ok=True)
    setting = setting_digest(countries, rivers, font) + hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    renderer = None
    drawn = 0
    for job in jobs:
        cached = cache / f"{fingerprint(job, setting)}.png"
        if not cached.exists():
            renderer = renderer or ConflictMapRenderer(countries, rivers, font)
            renderer.render_conflict(job)
            cached.write_bytes(job.output.read_bytes())
            drawn += 1
        else:
            job.output.parent.mkdir(parents=True, exist_ok=True)
            job.output.write_bytes(cached.read_bytes())
    logger.info("Drew %d conflict maps, reused %d", drawn, len(jobs) - drawn)
