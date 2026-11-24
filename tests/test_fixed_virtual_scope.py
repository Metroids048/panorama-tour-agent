"""Dated virtual walks retain source and direction requirements without promising access today."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixed_route import access_issue, grade_route, route_info


class FixedVirtualScope(unittest.TestCase):
    def setUp(self):
        self.raw=dict(start='gate',destination='lab',audience='virtual visitor',coverage='photographed route',
                      checked_on='2026-11-11',first_action='recognize the gate',
                      verify_url='https://example.com/source',verify_note='dated licensed photographs',
                      public_photos=True,visit_mode='virtual',captured_on='2026-07-20',access='historical')
        self.tour=dict(fixed_route=route_info(self.raw),stations=[
            dict(panorama={'image':'sphere'},photo_sources=[{'url':'https://example.com'}],
                 walk={j:dict(confirmed=True) for j in (i-1,i+1) if 0<=j<3}) for i in range(3)])

    def test_licensed_full_spheres_can_form_a_dated_virtual_route(self):
        self.assertTrue(grade_route(self.tour)['route_ready'])

    def test_virtual_scope_cannot_excuse_missing_source_date_or_photos(self):
        for key,value in [('captured_on',''),('captured_on','2099-01-01'),('public_photos',False),('access','open')]:
            raw=dict(self.raw,**{key:value}); self.assertTrue(access_issue(route_info(raw)),key)
        data=copy.deepcopy(self.tour);data['stations'][1]['panorama']=None
        self.assertFalse(grade_route(data)['route_ready'])
        data=copy.deepcopy(self.tour);data['stations'][1]['photo_sources']=[]
        self.assertFalse(grade_route(data)['route_ready'])
        data=copy.deepcopy(self.tour);data['stations'][0]['walk'][1]['confirmed']=False
        self.assertFalse(grade_route(data)['route_ready'])

    def test_current_site_walk_keeps_existing_access_and_expiry_checks(self):
        raw=dict(self.raw);raw.pop('visit_mode');raw.pop('captured_on');raw['access']='unknown'
        self.assertTrue(access_issue(route_info(raw)))
        raw.update(access='open',valid_until='2026-01-01')
        self.assertTrue(access_issue(route_info(raw)))


if __name__=='__main__':unittest.main()
