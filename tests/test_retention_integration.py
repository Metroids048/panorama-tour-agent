"""Exercise real save/render entry points in an isolated temporary workspace."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import app
import project
import retention


class SaveRetentionIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        for target, value in [('ROOT', self.workspace), ('PROJECTS', self.workspace / 'projects')]:
            p = patch.object(project, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(app, 'DOWNLOAD_DIR', self.workspace / 'openclaw-workspace/.downloads')
        p.start()
        self.addCleanup(p.stop)
        self.task = project.Project('isolated-retention-check')
        image = io.BytesIO()
        Image.new('RGB', (640, 480), '#758a91').save(image, 'JPEG')
        source = self.task.original('test.jpg', image.getvalue())
        self.data = dict(title='Retention software fixture', stations=[dict(id='room', files=[source])])

    def save(self, parent=None, portable=app.portable):
        return self.task.publish(copy.deepcopy(self.data), parent, app.prepare_station, portable)

    def test_real_five_saves_keep_one_and_failed_save_does_not_prune(self):
        parent = None
        with patch.object(retention.time, 'time', return_value=time.time() + 3600):
            for index in range(5):
                self.data['title'] = 'Software fixture %s' % index
                receipt = self.save(parent)
                parent = receipt['revision']
        self.assertEqual(len(list((self.task.root / 'versions').glob('*/tour.html'))), 1)
        self.assertEqual(len(list((self.task.root / 'versions').glob('*/receipt.json'))), 5)
        with patch.object(retention, 'prune_locked', wraps=retention.prune_locked) as prune:
            with self.assertRaisesRegex(ValueError, 'failed render'):
                self.save(parent, portable=lambda _: (_ for _ in ()).throw(ValueError('failed render')))
            prune.assert_not_called()
        self.assertEqual((self.task.root / 'latest').read_text().strip(), parent)
        self.assertTrue(Path(receipt['output']).exists())

    def test_download_head_failure_preserves_existing_stable_url(self):
        receipt = self.save()
        path = Path(receipt['output'])
        first = retention.ensure_download(path, app.DOWNLOAD_DIR)
        with patch.object(app, 'urlopen', side_effect=OSError('transient transport')):
            response = app.register_download(path, 18792, kind='看景草稿')
        self.assertIn('download_error', response)
        self.assertTrue(first.exists())
        self.assertEqual(retention.digest(first), receipt['sha256'])
        self.assertEqual(retention.ensure_download(path, app.DOWNLOAD_DIR), first)


if __name__ == '__main__':
    unittest.main()
