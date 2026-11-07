import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('pack_profile', ROOT / 'tools/packs/profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class PackProfileTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.packs = self.root / 'packs'
        self.packs.mkdir()
        workflow = (ROOT / '.github/workflows/publish-packs.yml').read_text()
        code = workflow.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
        self.publish_guard = '\n'.join(line[10:] for line in code.splitlines())

    def prepare(self, name):
        for pack in profile.PROFILES[name]:
            (self.packs / (pack + '.tar.zst')).write_bytes(('verified ' + pack).encode())
        profile.record(self.packs, name)

    def publish_check(self):
        return subprocess.run([sys.executable, '-c', self.publish_guard], cwd=self.root,
                              env=dict(os.environ, GITHUB_OUTPUT=str(self.root / 'output')),
                              capture_output=True, text=True)

    def assert_rejected(self):
        with self.assertRaises(ValueError):
            profile.verify(self.packs)
        self.assertNotEqual(self.publish_check().returncode, 0)

    def test_complete_steam_profile_passes_local_and_publication_validation(self):
        self.prepare('steam-only')
        self.assertEqual(profile.verify(self.packs, 'steam-only'), profile.PROFILES['steam-only'])
        result = self.publish_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'output').read_text(), 'names=proot directaudio tracking powervr\n')

    def test_complete_legacy_profile_remains_supported(self):
        self.prepare('all-stores')
        self.assertEqual(profile.verify(self.packs, 'all-stores'), profile.PROFILES['all-stores'])
        self.assertEqual(self.publish_check().returncode, 0)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            profile.verify(self.packs, 'steam-only')

    def test_missing_required_powervr_pack_is_rejected(self):
        self.prepare('steam-only')
        (self.packs / 'powervr.tar.zst').unlink()
        self.assert_rejected()

    def test_removed_required_entry_cannot_shrink_profile(self):
        self.prepare('steam-only')
        data = json.loads((self.packs / 'profile.json').read_text())
        del data['packs']['tracking']
        (self.packs / 'tracking.tar.zst').unlink()
        (self.packs / 'profile.json').write_text(json.dumps(data))
        self.assert_rejected()

    def test_cached_store_pack_cannot_leak_into_steam_profile(self):
        self.prepare('steam-only')
        (self.packs / 'xbox.tar.zst').write_bytes(b'cached xbox')
        self.assert_rejected()

    def test_modified_archive_is_rejected(self):
        self.prepare('steam-only')
        (self.packs / 'proot.tar.zst').write_bytes(b'changed proot')
        self.assert_rejected()

    def test_unknown_profile_is_rejected(self):
        self.prepare('steam-only')
        data = json.loads((self.packs / 'profile.json').read_text())
        data['profile'] = 'partial'
        (self.packs / 'profile.json').write_text(json.dumps(data))
        self.assert_rejected()

    def test_archive_symlink_is_rejected(self):
        self.prepare('steam-only')
        original = self.packs / 'proot.tar.zst'
        target = self.root / 'outside.tar.zst'
        original.replace(target)
        original.symlink_to(target)
        self.assert_rejected()
