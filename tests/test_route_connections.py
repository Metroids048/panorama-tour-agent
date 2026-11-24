"""A declared branched route must keep all directions and remain returnable."""
import copy
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from delivery import check_delivery, fulfillment, quality
from project import Project
from app import validate_links


class RouteConnections(unittest.TestCase):
    def setUp(self):
        self.pairs = [('a', 'b'), ('b', 'a'), ('a', 'c'), ('c', 'a')]
        self.raw = dict(visitor_route=True, entry='a', route_connections=[
            dict(from_id=a, to_id=b) for a, b in self.pairs], stations=[
                dict(id=k, files=[k + '.jpg'], spherical=True, links=[
                    dict(to_id=b, yaw=20, pitch=-20, view_revision='v',
                         confirmed_by='source and both ends', evidence='open stone passage')
                    for a, b in self.pairs if a == k]) for k in ['a', 'b', 'c']])
        self.stations = [dict(id=k, name=k, view=dict(width=6144, height=3072,
                         haov=360, vaov=180)) for k in ['a', 'b', 'c']]
        self.edges = [dict(zip(('from', 'to'), p), status='confirmed') for p in self.pairs]

    def assemble(self, raw):
        item = dict(panorama=dict(haov=360, vaov=180, vOffset=0), originals=[], view_revision='v')
        with patch.object(Project, 'station', side_effect=lambda *a: copy.deepcopy(item)):
            return Project('branch-import-check').assemble(raw, None)

    def test_non_adjacent_declared_directions_are_rendered(self):
        tour, edges = self.assemble(self.raw)
        self.assertEqual([e['to'] for e in tour['stations'][0]['links']], [1, 2])
        self.assertEqual(len(edges), 4)
        self.assertTrue(all(e['status'] == 'confirmed' for e in edges))

    def test_first_arrangement_binds_new_arrows_on_unchanged_first_look_spheres(self):
        previous = copy.deepcopy(self.raw)
        previous['first_look'] = True
        for station in previous['stations']:
            station['links'] = []
        for station in self.raw['stations']:
            for edge in station['links']:
                edge.pop('view_revision')
        item = dict(panorama=dict(haov=360, vaov=180, vOffset=0), originals=[], view_revision='v')
        with patch.object(Project, 'station', side_effect=lambda *a: copy.deepcopy(item)):
            _, edges = Project('first-look-binding').assemble(self.raw, None, previous=previous, bind_spheres=True)
        self.assertEqual([e['status'] for e in edges], ['confirmed'] * 4)

    def test_first_look_binding_does_not_rebind_changed_source_or_stale_anchor(self):
        previous = copy.deepcopy(self.raw)
        previous['first_look'] = True
        self.raw['stations'][0]['files'] = ['replacement.jpg']
        for edge in self.raw['stations'][0]['links']:
            edge.pop('view_revision')
        self.raw['stations'][1]['links'][0]['view_revision'] = 'old-frame'
        item = dict(panorama=dict(haov=360, vaov=180, vOffset=0), originals=[], view_revision='v')
        with patch.object(Project, 'station', side_effect=lambda *a: copy.deepcopy(item)):
            _, edges = Project('first-look-stale').assemble(self.raw, None, previous=previous, bind_spheres=True)
        self.assertEqual([e['status'] for e in edges if e['status'] != 'pending'][:3], ['stale-view'] * 3)

    def test_linear_input_still_requires_adjacent_directions(self):
        self.raw.pop('route_connections')
        _, edges = self.assemble(self.raw)
        self.assertIn('not-adjacent', [e['status'] for e in edges])
        self.assertFalse(quality(self.stations, edges)['route_ready'])

    def test_actual_export_validates_declared_branch_without_linear_rejection(self):
        tour, _ = self.assemble(self.raw)
        validate_links(tour)
        undeclared = copy.deepcopy(tour)
        undeclared['route_connections'] = undeclared['route_connections'][:2]
        with self.assertRaises(ValueError): validate_links(undeclared)
        linear = copy.deepcopy(tour)
        linear.pop('route_connections')
        with self.assertRaisesRegex(ValueError, '相邻站'): validate_links(linear)

    def test_export_rejects_malformed_branch_and_non_world_links(self):
        tour, _ = self.assemble(self.raw)
        for changes in [dict(route_connections=[]), dict(visitor_route=False),
                        dict(route_connections=[dict(**{'from': 0, 'to': 99})]),
                        dict(fixed_route={'destination': 2}), dict(forward_only=True)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_links(dict(tour, **changes))
        for link in [dict(to=1, cut=True), dict(to=1, photo=0, x=.5, y=.5)]:
            bad = copy.deepcopy(tour)
            bad['stations'][0]['links'] = [link]
            with self.subTest(link=link), self.assertRaises(ValueError): validate_links(bad)

    def test_branch_grade_accepts_all_confirmed_returnable_stations(self):
        grade = quality(self.stations, self.edges, required=self.pairs, entry='a')
        self.assertTrue(grade['route_ready'])
        self.assertEqual((grade['arrows'], grade['required']), (4, 4))

    def test_missing_return_stale_anchor_cut_and_photo_never_complete(self):
        for change in ['missing', 'stale-view', 'cut', 'photo']:
            edges = copy.deepcopy(self.edges)
            if change == 'missing': edges.pop()
            elif change == 'photo': edges[-1]['photo_only'] = True
            else: edges[-1]['status'] = change
            with self.subTest(change=change):
                grade = quality(self.stations, edges, required=self.pairs, entry='a')
                self.assertFalse(grade['route_ready'])
                with self.assertRaises(ValueError): check_delivery({}, grade, True)

    def test_isolated_station_fails_even_if_declared_edges_complete(self):
        grade = quality(self.stations, self.edges[:2], required=self.pairs[:2], entry='a')
        self.assertFalse(grade['route_ready'])

    def test_partial_gap_uses_declared_branch_not_array_neighbor(self):
        tour, _ = self.assemble(self.raw)
        tour['stations'][0]['links'] = [e for e in tour['stations'][0]['links'] if e['to'] != 2]
        result = fulfillment(self.raw, tour, dict(panorama_ready=True, route_ready=False))
        self.assertEqual(result['first_gap'], dict(from_id='a', to_id='c'))

    def test_invalid_modes_and_targets_are_rejected(self):
        for changes in [dict(visitor_route=False), dict(forward_only=True),
                        dict(route_connections=[dict(from_id='a', to_id='missing')]),
                        dict(route_connections=[]), dict(route_connections=[dict(from_id='a', to_id='a')])]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.assemble(dict(self.raw, **changes))

    def test_unlisted_arrow_does_not_bypass_declaration(self):
        self.raw['route_connections'] = self.raw['route_connections'][:2]
        tour, edges = self.assemble(self.raw)
        self.assertEqual([e['to'] for e in tour['stations'][0]['links']], [1])
        self.assertIn('undeclared', [e['status'] for e in edges])


if __name__ == '__main__': unittest.main()
