"""A fresh Windows process can discover WinGet user install binaries without PATH."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from holding.winget_binary import discover_winget_binary
from holding.windows_package_manager import WingetPin

class WingetBinaryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.git=self.root/"Programs"/"Git"/"cmd"/"git.exe"
        self.git.parent.mkdir(parents=True)
        self.git.write_bytes(b"fixture")
        self.pin=WingetPin("git","Git.Git","2.55.0")

    def discover(self,pin=None):
        with patch("holding.winget_binary.platform.system",return_value="Windows"):
            with patch.dict("holding.winget_binary.os.environ",
                            {"LOCALAPPDATA":str(self.root)}):
                return discover_winget_binary(pin or self.pin)

    def test_exact_version_on_known_exe_without_path(self):
        with patch("holding.winget_binary.run_probe",
                   return_value=subprocess.CompletedProcess(
                       [],0,"git version 2.55.0.windows.1","")) as run:
            found=self.discover()
        self.assertEqual(found.state,"EXACT_BINARY_VERIFIED")
        self.assertEqual(found.executable,str(self.git))
        self.assertEqual(run.call_args.args[0],[str(self.git),"--version"])
        self.assertEqual(run.call_args.kwargs["max_output_bytes"],8192)

    def test_missing_path_does_not_claim_found(self):
        self.git.unlink()
        with patch("holding.winget_binary.run_probe") as run:
            found=self.discover()
        self.assertEqual(found.state,"NO_PINNED_BINARY_FOUND")
        run.assert_not_called()

    def test_wrong_version_cannot_validate(self):
        with patch("holding.winget_binary.run_probe",
                   return_value=subprocess.CompletedProcess(
                       [],0,"git version 2.55.01","")):
            found=self.discover()
        self.assertEqual(found.state,"NO_PINNED_BINARY_FOUND")

    def test_other_user_profile_is_not_searched(self):
        with patch("holding.winget_binary.platform.system",return_value="Windows"):
            with patch.dict("holding.winget_binary.os.environ",
                            {"LOCALAPPDATA":"relative"},clear=True):
                result=discover_winget_binary(self.pin)
        self.assertEqual(result.state,"LOCALAPPDATA_UNAVAILABLE")

    def test_opencode_portable_payload_candidate(self):
        folder=self.root/"Microsoft"/"WinGet"/"Packages"/"SST.opencode__winget"
        folder.mkdir(parents=True)
        binary=folder/"opencode.exe"
        binary.write_bytes(b"fixture")
        pin=WingetPin("opencode","SST.opencode","1.18.29")
        with patch("holding.winget_binary.run_probe",
                   return_value=subprocess.CompletedProcess(
                       [],0,"opencode 1.18.29","")):
            found=self.discover(pin)
        self.assertEqual(found.state,"EXACT_BINARY_VERIFIED")
        self.assertEqual(found.executable,str(binary))

    def test_symlinked_native_binary_is_not_run(self):
        original=self.root/"actual.exe"
        original.write_bytes(b"fixture")
        self.git.unlink()
        try:
            self.git.symlink_to(original)
        except (NotImplementedError,OSError):
            self.skipTest("Symlink unavailable")
        with patch("holding.winget_binary.run_probe") as run:
            result=self.discover()
        self.assertEqual(result.state,"NO_PINNED_BINARY_FOUND")
        run.assert_not_called()

if __name__=="__main__":
    unittest.main()
