"""Pin checks are repeated at dispatch time, not only during installation."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from holding.process_agent import AgentProgram, AgentProcessError, LocalProcessAgent


def sha256(path):
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


class AgentFilePinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = Path(sys.executable).resolve()
        self.script = self.root / 'trusted.py'
        self.script.write_text(
            "import json,sys\n"
            "request=json.load(sys.stdin)\n"
            "print(json.dumps({'protocol':1,'action':request['action'],'status':'ok'}))\n",
            encoding='utf-8')

    def program(self, *, executable_hash=None, script_hash=None):
        return AgentProgram(
            self.executable, (str(self.script),),
            timeout_seconds=3,
            pinned_executable_sha256=executable_hash or sha256(self.executable),
            pinned_arguments=((str(self.script), script_hash or sha256(self.script)),))

    def test_script_mutation_after_first_success_refused(self):
        agent = LocalProcessAgent(self.program())
        self.assertEqual(agent.request('health', {}, cwd=self.root)['status'], 'ok')
        self.script.write_text("print('changed')\n", encoding='utf-8')
        with self.assertRaisesRegex(AgentProcessError, 'changed after installation'):
            agent.request('health', {}, cwd=self.root)

    def test_mismatched_executable_digest_refused(self):
        agent = LocalProcessAgent(self.program(executable_hash='0' * 64))
        with self.assertRaisesRegex(AgentProcessError, 'changed after installation'):
            agent.request('health', {}, cwd=self.root)

    def test_symlinked_script_refused_even_if_digest_matches(self):
        link = self.root / 'link.py'
        try:
            link.symlink_to(self.script)
        except OSError:
            self.skipTest('Symlinks unavailable')
        program = AgentProgram(
            self.executable, (str(link),), timeout_seconds=3,
            pinned_executable_sha256=sha256(self.executable),
            pinned_arguments=((str(link), sha256(self.script)),))
        with self.assertRaisesRegex(AgentProcessError, 'pin is invalid'):
            LocalProcessAgent(program).request('health', {}, cwd=self.root)

    def test_partial_or_inconsistent_installer_pins_refused(self):
        with self.assertRaises(ValueError):
            AgentProgram(self.executable, (str(self.script),),
                         pinned_executable_sha256=sha256(self.executable))
        with self.assertRaises(ValueError):
            AgentProgram(self.executable, pinned_arguments=((str(self.script), '0'*64),))


if __name__ == '__main__':
    unittest.main()
