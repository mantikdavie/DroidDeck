import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = 'com.droiddeck.launcher'


class SignApkRenamesTest(unittest.TestCase):
    def test_steam_apk_cannot_enter_the_official_all_store_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            aapt = work / 'aapt'
            aapt.write_text("#!/bin/sh\necho \"package: name='com.droiddeck.launcher.steam' versionCode='17'\"\n")
            aapt.chmod(0o755)
            environment = {key: value for key, value in os.environ.items() if not key.startswith('RELEASE_')}
            environment['BUILD_TOOLS'] = str(work)
            variants = (ROOT / 'tools/release/variants.txt').read_text().splitlines()
            for row in variants:
                if not row.strip() or row.startswith('#'):
                    continue
                with self.subTest(variant=row.split()[0]):
                    result = subprocess.run(
                        ['bash', str(ROOT / 'tools/release/sign-apk.sh'), str(work / 'input.apk'),
                         row.split()[0], str(work / 'signed.apk')],
                        env=environment, capture_output=True, text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('require a dedicated release catalog', result.stderr)
                    self.assertFalse((work / 'signed.apk').exists())

    def test_variants_rename_every_authority_and_task_named_after_the_package(self):
        manifest = (ROOT / 'app/src/main/AndroidManifest.xml').read_text()
        names = set()
        for attribute in ('authorities', 'taskAffinity'):
            for value in re.findall(r'android:%s="([^"]*)"' % attribute, manifest):
                self.assertNotIn(';', value, 'sign-apk.sh renames whole values, so give each authority its own provider')
                name = value.strip().replace('${applicationId}', PACKAGE)
                if name.startswith(PACKAGE + '.'):
                    names.add(name[len(PACKAGE):])
        self.assertIn('.agent', names)
        script = (ROOT / 'tools/release/sign-apk.sh').read_text()
        renamed = set(re.findall(r'"\$P(\.[A-Za-z0-9_.-]+)=\$pkg\1"', script))
        self.assertEqual(set(), names - renamed, 'add these to the axml_rename call in tools/release/sign-apk.sh')


if __name__ == '__main__':
    unittest.main()
