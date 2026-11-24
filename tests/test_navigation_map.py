"""Map validation and station-cap regressions; software checks, not site verification."""
import copy
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from project import Project, MAX_STATIONS
from venue import navigation_map, overview


class NavigationMap(unittest.TestCase):
    def setUp(self):
        image = io.BytesIO()
        Image.new("RGB", (180, 100), "white").save(image, "PNG")
        self.image = image.getvalue()
        self.spec = {"name": "已确认位置图", "source": "上传者确认编号位置", "plan": "map.png",
                     "places": [{"station": "gate", "x": .2, "y": .4}]}

    def compile(self, spec):
        return navigation_map(spec, ["gate", "hall"], lambda path: self.image)

    def test_plan_is_embedded_and_station_id_resolved(self):
        result = self.compile(self.spec)
        self.assertTrue(result["plan"].startswith("data:image/png;base64,"))
        self.assertEqual(result["places"], [{"station": 0, "x": .2, "y": .4}])
        self.assertEqual(self.spec["places"][0]["station"], "gate")

    def test_confirmed_space_schematic_renders_offline_without_agent_writing_code(self):
        spec = dict(self.spec, kind='schematic', schematic={
            'areas': [{'name': '入口', 'polygon': [[.1,.2],[.4,.2],[.4,.8],[.1,.8]]},
                      {'name': '展厅', 'polygon': [[.4,.2],[.9,.2],[.9,.8],[.4,.8]]}],
            'paths': [[[.25,.5],[.65,.5]]]})
        spec.pop('plan')
        result = self.compile(spec)
        self.assertEqual(result['kind'], 'schematic')
        with Image.open(io.BytesIO(__import__('base64').b64decode(result['plan'].split(',')[1]))) as image:
            self.assertEqual(image.size, (1200, 900))
            self.assertGreater(len(image.getcolors(1200 * 900)), 3)
        for change in [dict(kind='located'), dict(schematic={'areas': []}),
                       dict(schematic={'areas': [{'name':'bad','polygon':[[0,0],[1,1],[2,0]]}]}),
                       dict(plan='also.png')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.compile(dict(spec, **change))

    def test_map_basis_is_saved_and_invalid_types_rejected(self):
        self.assertEqual(self.compile(self.spec)["kind"], "located")
        self.assertEqual(self.compile(dict(self.spec, kind="schematic"))["kind"], "schematic")
        for kind in ("gps-live", "", None, {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.compile(dict(self.spec, kind=kind))

    def test_venue_floor_map_basis_survives_compilation(self):
        spec = {"coverage": "reviewed floor", "levels": [dict(self.spec, id="one", floor="1F", kind="schematic")],
                "places": [{"station": "gate", "level": "one", "x": .2, "y": .4}]}
        spec["levels"][0].pop("places")
        result, _ = overview(spec, [{"id": "gate"}], lambda path: self.image, lambda key: key)
        self.assertEqual(result["levels"][0]["kind"], "schematic")
        self.assertEqual(result["levels"][0]["places"][0]["x"], .2)
        spec["levels"][0]["kind"] = "gps-live"
        with self.assertRaises(ValueError):
            overview(spec, [{"id": "gate"}], lambda path: self.image, lambda key: key)

    def test_venue_uses_same_declared_schematic_and_rejects_image_crop(self):
        level = dict(id='one', name='一层', floor='1F', kind='schematic',
                     source='已核实相邻空间', schematic={'areas': [
                         {'name': '大厅', 'polygon': [[.1,.1],[.9,.1],[.9,.9],[.1,.9]]}]})
        spec = dict(coverage='一层大厅', levels=[level],
                    places=[dict(station='gate', level='one', x=.5, y=.5)])
        result, _ = overview(spec, [{'id': 'gate'}], lambda path: self.image, lambda key: key)
        self.assertTrue(result['levels'][0]['plan'].startswith('data:image/png;base64,'))
        self.assertEqual(result['levels'][0]['places'][0]['x'], .5)
        level['plan_crop'] = [.1, .1, .9, .9]
        with self.assertRaises(ValueError):
            overview(spec, [{'id': 'gate'}], lambda path: self.image, lambda key: key)

    def test_bad_locations_are_rejected(self):
        for value in (-.1, 1.1, float("nan"), float("inf"), True, "0.5", None):
            spec = copy.deepcopy(self.spec)
            spec["places"][0]["x"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compile(spec)

    def test_unknown_duplicate_and_unlocated_points_are_rejected(self):
        for places in ([{"station": "other", "x": .1, "y": .1}],
                       self.spec["places"] * 2, [{"station": "gate"}], [None]):
            with self.subTest(places=places), self.assertRaises(ValueError):
                self.compile(dict(self.spec, places=places))

    def test_source_and_shape_are_required(self):
        for spec in ([], {}, dict(self.spec, source="")):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.compile(spec)

    def test_photo_coordinates_follow_input_binding_after_station_reorder(self):
        spec = copy.deepcopy(self.spec)
        spec["places"][0]["source_file"] = "gate.jpg"
        stations = [{"id": "hall", "files": ["hall.jpg"]}, {"id": "gate", "files": ["gate.jpg"]}]
        result = navigation_map(spec, ["hall", "gate"], lambda path: self.image, stations)
        self.assertEqual(result["places"][0]["station"], 1)
        stations[1]["files"] = ["different-view.jpg"]
        with self.assertRaisesRegex(ValueError, "原片与站点不匹配"):
            navigation_map(spec, ["hall", "gate"], lambda path: self.image, stations)

    def test_embedded_names_are_preserved_and_must_be_boolean(self):
        self.assertTrue(self.compile(dict(self.spec, labels_embedded=True))["labels_embedded"])
        with self.assertRaises(ValueError):
            self.compile(dict(self.spec, labels_embedded="true"))

    def test_excess_stations_rejected_before_processing(self):
        self.assertEqual(MAX_STATIONS, 32)
        with self.assertRaisesRegex(ValueError, "32"):
            Project("capacity-check").assemble({"stations": [{}] * 33}, None)


if __name__ == "__main__":
    unittest.main()
