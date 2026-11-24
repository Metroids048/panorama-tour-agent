"""Large-sphere inspection uses bounded views with calibrated yaw labels."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image


class SphereInspectionViews(unittest.TestCase):
    def test_near_two_to_one_museum_sphere_gets_inspection_views(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'museum.jpg'
            Image.new('RGB', (5640, 2816), '#398b66').save(source)
            script = Path(__file__).resolve().parents[1] / 'openclaw-sandbox/agent/inspect_spheres.py'
            if not script.exists():
                script = Path(__file__).resolve().parents[1] / 'inspect_spheres.py'
            rows = json.loads(subprocess.check_output([sys.executable, str(script), str(source), str(root / 'views')]))
            self.assertEqual(len([r for r in rows if r.get('inspection_only')]), 16)

    def test_single_sphere_views_have_real_direction_and_directory_contract_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / 'photos'
            photos.mkdir()
            # Longitude-coded color strips make opposite directions distinct.
            pixels = np.zeros((360, 720, 3), dtype=np.uint8)
            pixels[:, :180] = (220, 20, 20)
            pixels[:, 180:360] = (20, 220, 20)
            pixels[:, 360:540] = (20, 20, 220)
            pixels[:, 540:] = (220, 220, 20)
            pixels[240:] = (210, 20, 210)
            source = photos / 'sphere.png'
            Image.fromarray(pixels).save(source)
            script = Path(__file__).resolve().parents[1] / 'openclaw-sandbox/agent/inspect_spheres.py'
            if not script.exists():
                script = Path(__file__).resolve().parents[1] / 'inspect_spheres.py'

            def run(path, output):
                return json.loads(subprocess.check_output([sys.executable, str(script), str(path), str(output)]))

            old = run(photos, root / 'board')
            self.assertEqual(len(old), 1)
            views = run(source, root / 'detail')
            samples = {v['yaw']: v for v in views if v.get('inspection_only') and v['pitch'] == 0}
            lower = [v for v in views if v.get('inspection_only') and v['pitch'] == -45]
            self.assertEqual(len(lower), 8)
            with Image.open(lower[4]['preview']) as view:
                actual_lower = view.getpixel((480, 352))
            self.assertTrue(all(abs(a-b) < 12 for a, b in zip(actual_lower, (210, 20, 210))))
            self.assertEqual(set(samples), set(range(-180, 180, 45)))
            for yaw, expected in [(-135, (220, 20, 20)), (45, (20, 20, 220))]:
                with Image.open(samples[yaw]['preview']) as view:
                    self.assertEqual(view.size, (960, 672))
                    actual = view.getpixel((480, 352))
                self.assertTrue(all(abs(a-b) < 12 for a, b in zip(actual, expected)))

            index_dir = root / 'index'
            index_result = json.loads(subprocess.check_output([
                sys.executable, str(script), str(source), str(index_dir), '--route-index']))
            overview, = [row for row in index_result if row.get('route_index')]
            self.assertEqual(overview['yaws'], list(range(-180, 180, 45)))
            self.assertEqual(sorted(p.name for p in index_dir.glob('*.jpg')), ['1.jpg', 'route-index.jpg'])
            with Image.open(overview['route_index']) as board:
                self.assertEqual(board.size, (1920, 672))
                for center, expected in [((720, 176), (220, 20, 20)), ((720, 512), (20, 20, 220))]:
                    actual = board.getpixel(center)
                    self.assertTrue(all(abs(a-b) < 12 for a, b in zip(actual, expected)))

            requested = json.loads(subprocess.check_output([sys.executable, str(script), str(source),
                str(root / 'anchor'), '--view', '62.5', '-12.5']))
            anchor, = [row for row in requested if row.get('inspection_only')]
            self.assertEqual((anchor['yaw'], anchor['pitch']), (62.5, -12.5))
            with Image.open(anchor['preview']) as view:
                self.assertGreater(min(view.getpixel((494, 352))), 200)

    def test_intake_dimensions_follow_exif_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / 'photos'
            photos.mkdir()
            exif = Image.Exif()
            exif[274] = 6
            Image.new('RGB', (360, 720)).save(photos / 'camera.jpg', exif=exif)
            script = Path(__file__).resolve().parents[1] / 'openclaw-sandbox/agent/inspect_spheres.py'
            if not script.exists():
                script = Path(__file__).resolve().parents[1] / 'inspect_spheres.py'
            row, = json.loads(subprocess.check_output([sys.executable, str(script), str(photos), str(root / 'boards')]))
            self.assertEqual((row['width'], row['height']), (720, 360))


if __name__ == '__main__':
    unittest.main()
