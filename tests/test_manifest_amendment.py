"""Small metadata changes preserve the exact saved route and its guards."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project import Project


class ManifestAmendment(unittest.TestCase):
    def setUp(self):
        self.project = Project('amendment-check')
        self.parent = '1' * 32
        self.raw = dict(project=self.project.key, revision=self.parent,
                        request=dict(mode='route', purpose='keep the complete return route'),
                        archive_notice='historical draft', destination='b',
                        stations=[dict(id='a', files=['originals/a/a.jpg'], links=[dict(to_id='b')]),
                                  dict(id='b', files=['originals/b/b.jpg'], links=[dict(to_id='a')])])

    def resolve(self, amend, **envelope):
        value = dict(project=self.project.key, revision=self.parent, amend=amend, **envelope)
        with patch.object(self.project, 'read', return_value=(self.raw, Path('.'))):
            return self.project.amend_manifest(value, self.parent)

    def test_exact_parent_metadata_update_preserves_route_and_original(self):
        before = copy.deepcopy(self.raw)
        result = self.resolve(dict(archive_notice=None, destination=None,
                                   historical_visit=dict(captured_on='2026-07-20', source='licensed'),
                                   stations=[dict(id='a', walk={'b': {'see': 'door'}})]))
        self.assertNotIn('archive_notice', result)
        self.assertNotIn('destination', result)
        self.assertEqual(result['stations'][0]['walk']['b']['see'], 'door')
        self.assertEqual(result['stations'][1], before['stations'][1])
        self.assertEqual(result['request'], before['request'])
        self.assertEqual(result['stations'][0]['links'], before['stations'][0]['links'])
        self.assertEqual(self.raw, before)

    def test_route_and_request_changes_unknown_or_duplicate_stations_rejected(self):
        for amendment in (dict(request={}), dict(stations=[dict(id='a', links=[])]),
                          dict(stations=[dict(id='a', files=[])]),
                          dict(stations=[dict(id='missing', walk={})]),
                          dict(stations=[dict(id='a'), dict(id='a')])):
            with self.subTest(amendment=amendment), self.assertRaises(ValueError):
                self.resolve(amendment)

    def test_parent_and_envelope_are_not_implicit(self):
        for value in (dict(project=self.project.key, revision='2'*32, amend={}),
                      dict(project='other', revision=self.parent, amend={}),
                      dict(project=self.project.key, revision=self.parent, amend={}, stations=[])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.project.amend_manifest(value, self.parent)

    def test_name_and_existing_anchor_patch_preserve_unmentioned_edges_and_sources(self):
        self.raw['stations'][0]['links'] = [dict(to_id='b', yaw=10, pitch=-5,
            view_revision='frame', cue='door', evidence='old'), dict(to_id='c', yaw=20)]
        before = copy.deepcopy(self.raw)
        result = self.resolve(dict(stations=[dict(id='a', name='Entrance', links=[
            dict(to_id='b', yaw=15, pitch=-8, view_revision='frame', cue='paved gap', evidence='checked')])]))
        self.assertEqual(result['stations'][0]['name'], 'Entrance')
        self.assertEqual(result['stations'][0]['links'][0]['yaw'], 15)
        self.assertEqual(result['stations'][0]['links'][0]['view_revision'], 'frame')
        self.assertEqual(result['stations'][0]['links'][1], before['stations'][0]['links'][1])
        self.assertEqual(result['stations'][0]['files'], before['stations'][0]['files'])
        self.assertEqual(result['stations'][1], before['stations'][1])
        self.assertEqual(self.raw, before)

    def test_anchor_patch_cannot_add_remove_rebind_or_duplicate_edges(self):
        self.raw['stations'][0]['links'][0]['view_revision'] = 'frame'
        for fields in (dict(name=''), dict(name=3), dict(links=[]),
                       dict(links=[dict(to_id='missing', yaw=2)]),
                       dict(links=[dict(to_id='b', view_revision='other')]),
                       dict(links=[dict(to_id='b', arbitrary='value')]),
                       dict(links=[dict(to_id='b'), dict(to_id='b')])):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.resolve(dict(stations=[dict(id='a', **fields)]))

    def test_map_artwork_patch_preserves_coordinates_and_route(self):
        self.raw['navigation_map'] = dict(plan='old.png', source='old source',
            places=[dict(station='a', x=.2, y=.3)])
        result = self.resolve(dict(navigation_map=dict(plan='unlabelled.png', source='same GPS points')))
        self.assertEqual(result['navigation_map']['plan'], 'unlabelled.png')
        self.assertEqual(result['navigation_map']['places'], self.raw['navigation_map']['places'])
        self.assertEqual(result['stations'], self.raw['stations'])
        with self.assertRaises(ValueError):
            self.resolve(dict(navigation_map=dict(places=[])))
        with self.assertRaises(ValueError):
            self.resolve(dict(navigation_map=None))


if __name__ == '__main__':
    unittest.main()
