import os
import unittest
from holding.windows_job import WindowsJob, WindowsJobError


class WindowsJobTests(unittest.TestCase):
    def test_non_windows_raises_instead_of_pretending_protected(self):
        if os.name == 'nt':
            self.skipTest('Runs only on non-Windows test host')
        with self.assertRaises(WindowsJobError) as raised:
            WindowsJob()
        self.assertEqual(raised.exception.code, 'UNSUPPORTED_PLATFORM')

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_actual_win32_job_can_be_created_and_closed(self):
        with WindowsJob() as job:
            self.assertIsNotNone(job._handle)
            self.assertIsNone(job.max_job_memory_bytes)
        self.assertIsNone(job._handle)

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_empty_windows_job_peak_is_nonnegative(self):
        with WindowsJob() as job:
            self.assertGreaterEqual(job.peak_job_memory_bytes(), 0)
        with self.assertRaises(WindowsJobError) as raised:
            job.peak_job_memory_bytes()
        self.assertEqual(raised.exception.code, 'QUERY_CLOSED_JOB')

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_memory_quota_configures_a_real_job_object(self):
        with WindowsJob(max_job_memory_bytes=256 * 1024 ** 2) as job:
            self.assertIsNotNone(job._handle)
            self.assertEqual(job.max_job_memory_bytes, 256 * 1024 ** 2)
        for invalid in (0, True, -1, 1024 ** 3 + 1):
            with self.subTest(limit=invalid), self.assertRaises(WindowsJobError) as raised:
                WindowsJob(max_job_memory_bytes=invalid)
            self.assertEqual(raised.exception.code, 'INVALID_MEMORY_QUOTA')


if __name__ == '__main__':
    unittest.main()
