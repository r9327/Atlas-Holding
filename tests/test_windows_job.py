import os
import unittest
from holding.windows_job import WindowsJob, WindowsJobError


class WindowsJobTests(unittest.TestCase):
    def test_non_windows_raises_instead_of_pretending_protected(self):
        if os.name == 'nt':
            self.skipTest('Runs only on non-Windows test host')
        with self.assertRaises(WindowsJobError):
            WindowsJob()

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_actual_win32_job_can_be_created_and_closed(self):
        with WindowsJob() as job:
            self.assertIsNotNone(job._handle)
        self.assertIsNone(job._handle)


if __name__ == '__main__':
    unittest.main()
