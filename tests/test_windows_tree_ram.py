"""Process-tree RAM accounting tests; no mocked production certification."""
from __future__ import annotations

import os
import time
import unittest

from holding.windows_tree_ram import (
    MemorySample, MemorySampler, ProcessMemory, account_tree,
    windows_tree_sample,
)


class TreeMemoryTests(unittest.TestCase):
    def test_all_descendants_counted_once_and_inference_separate(self):
        table = (
            ProcessMemory(10, 1, "AtlasHoldingEngine.exe", 80),
            ProcessMemory(11, 10, "AtlasHoldingControlCenter.exe", 40),
            ProcessMemory(12, 11, "git.exe", 20),
            ProcessMemory(13, 10, "ollama.exe", 2000),
            ProcessMemory(14, 13, "plugin-host.exe", 30),
            ProcessMemory(15, 400, "unrelated.exe", 9000),
            ProcessMemory(16, 12, "doctor.exe", 10),
        )
        result = account_tree(table, {10, 11})
        self.assertEqual(result.holding_bytes, 180)
        self.assertEqual(result.model_bytes, 2000)
        self.assertEqual(result.included_pids, (10, 11, 12, 14, 16))
        self.assertEqual(result.model_pids, (13,))
        self.assertEqual(account_tree(table, {10}), result)
        self.assertEqual(account_tree(table, {15}).holding_bytes, 9000)

    def test_unknown_llm_auxiliary_is_not_hidden(self):
        sample = account_tree((
            ProcessMemory(1, 0, "AtlasHoldingEngine.exe", 10),
            ProcessMemory(2, 1, "new-model-wrapper.exe", 90),
        ), {1})
        self.assertEqual((sample.holding_bytes, sample.model_bytes), (100, 0))

    def test_invalid_snapshot_fails_closed(self):
        for data in (
            (ProcessMemory(1, 0, "engine.exe", -1),),
            (ProcessMemory(1, 0, "engine.exe", 20),
             ProcessMemory(1, 0, "dup.exe", 40)),
            (ProcessMemory(1, 0, "", 3),),
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                account_tree(data, {1})
        for roots in ({}, {0}, {-1}):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                account_tree((), roots)
        self.assertEqual(account_tree((), {123}).holding_bytes, 0)

    def test_sampler_peak_separates_model_and_requires_all_roots(self):
        calls = []
        def sample(roots):
            calls.append(roots)
            return MemorySample(
                len(calls) * 100, len(calls) * 200,
                tuple(sorted(roots)), (),
            )
        monitor = MemorySampler(interval=0.02, sample=sample)
        monitor.add_root(501)
        monitor.add_root(502)
        monitor.start()
        time.sleep(0.07)
        receipt = monitor.finish()
        self.assertGreaterEqual(receipt["samples"], 1)
        self.assertEqual(receipt["root_pids_observed"], 2)
        self.assertGreater(receipt["peak_holding_working_set_bytes"], 0)
        self.assertEqual(receipt["peak_model_working_set_bytes"],
                         2 * receipt["peak_holding_working_set_bytes"])
        self.assertIs(receipt["normal_coding_certified"], False)
        self.assertIs(receipt["release_ready"], False)

    def test_missing_root_cannot_claim_measurement(self):
        monitor = MemorySampler(
            interval=0.02,
            sample=lambda roots: MemorySample(50, 0, (2,), ()),
        )
        monitor.add_root(1)
        monitor.start()
        time.sleep(0.04)
        with self.assertRaisesRegex(RuntimeError, "Missing required"):
            monitor.finish()

    def test_sampler_error_never_claims_valid_memory(self):
        def failure(_roots):
            raise OSError("simulated Windows access denied")
        monitor = MemorySampler(interval=0.02, sample=failure)
        monitor.add_root(10)
        monitor.start()
        time.sleep(0.04)
        with self.assertRaisesRegex(RuntimeError, "sample failed"):
            monitor.finish()

    @unittest.skipUnless(os.name == "nt", "Real Win32 memory API only")
    def test_native_snapshot_counts_current_python_process(self):
        pid = os.getpid()
        data = windows_tree_sample({pid})
        self.assertIn(pid, data.included_pids)
        self.assertGreater(data.holding_bytes, 1024 * 1024)
        self.assertEqual(data.model_bytes, 0)


if __name__ == "__main__":
    unittest.main()
