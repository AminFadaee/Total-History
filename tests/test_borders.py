import shutil

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from total_history.http import Http
from total_history.sources.naturalearth import recognised_borders

RUSSIA = box(0, 0, 10, 10)
CRIMEA = box(4, 4, 6, 6)
UKRAINE = box(-5, 4, 0, 6)


@pytest.fixture
def http(tmp_path):
    disputed = gpd.GeoDataFrame({"BRK_NAME": ["Crimea", "Abkhazia"]}, geometry=[CRIMEA, box(8, 8, 9, 9)], crs=4326)
    (tmp_path / "layer").mkdir()
    disputed.to_file(tmp_path / "layer" / "disputed.shp")
    shutil.make_archive(str(tmp_path / "downloads" / "ne_50m_admin_0_breakaway_disputed_areas"), "zip", tmp_path / "layer")
    return Http(tmp_path)


@pytest.fixture
def countries():
    return gpd.GeoDataFrame({"ADMIN": ["Russia", "Ukraine"]}, geometry=[RUSSIA, UKRAINE], crs=4326)


def holder(frame, shapes, point):
    return next(admin for admin, shape in zip(frame.ADMIN, shapes) if shape.contains(Point(point)))


def test_crimea_moves_from_russia_to_ukraine(countries, http):
    shapes = recognised_borders(countries, http)
    assert holder(countries, shapes, (5, 5)) == "Ukraine"
    assert holder(countries, shapes, (2, 2)) == "Russia"
    assert holder(countries, shapes, (-2, 5)) == "Ukraine"


def test_only_crimea_moves_and_no_land_is_lost(countries, http):
    shapes = recognised_borders(countries, http)
    assert holder(countries, shapes, (8.5, 8.5)) == "Russia"
    assert sum(shape.area for shape in shapes) == pytest.approx(sum(shape.area for shape in countries.geometry))
    assert shapes.iloc[0].intersection(shapes.iloc[1]).area == 0
