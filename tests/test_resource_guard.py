import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from collections import namedtuple

from holding.resource_guard import ResourceGuard, available_ram

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_guard_reads_real_disk_and_ram(self):
        guard=ResourceGuard(self.root,min_free_disk=0,min_free_ram=0)
        report=guard.check()
        self.assertEqual(report.reason,'OK')
        self.assertGreater(report.free_disk,0)
        self.assertGreater(report.free_ram,0)

    def test_disk_low_blocks_work(self):
        with patch('holding.resource_guard.shutil.disk_usage',return_value=namedtuple('Usage','total used free')(200,190,10)):
            self.assertEqual(ResourceGuard(self.root,min_free_disk=100).check().reason,'DISK_PRESSURE')

    def test_ram_low_blocks_work(self):
        with patch('holding.resource_guard.available_ram',return_value=3):
            self.assertEqual(ResourceGuard(self.root,min_free_disk=0,min_free_ram=100).check().reason,'RAM_PRESSURE')

    def test_ram_unknown_blocks_work(self):
        with patch('holding.resource_guard.available_ram',return_value=None):
            self.assertEqual(ResourceGuard(self.root,min_free_disk=0).check().reason,'RAM_UNKNOWN')

    def test_disk_check_failure_blocks_work(self):
        with patch('holding.resource_guard.shutil.disk_usage',side_effect=OSError('device')):
            self.assertEqual(ResourceGuard(self.root).check().reason,'DISK_UNKNOWN')

    def test_invalid_threshold_denied(self):
        with self.assertRaises(ValueError):ResourceGuard(self.root,min_free_ram=-1)

if __name__=='__main__':unittest.main()
