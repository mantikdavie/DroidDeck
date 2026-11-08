import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('dxvk_component',
        Path(__file__).resolve().parents[1] / 'dxvk/component.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


class DxvkComponentSourceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Component Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.source / 'LICENSE').write_bytes(b'fixture source license\n')
        (self.source / 'shader.cpp').write_bytes(b'int debug_names = 1;\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture source')
        self.revision = self.git('rev-parse', 'HEAD').decode().strip()
        recipe = self.root / 'tools/dxvk'
        (recipe / 'patches').mkdir(parents=True)
        (recipe / 'source.env').write_text('DXVK_REVISION=' + self.revision + '\n')
        (self.source / 'shader.cpp').write_bytes(b'int debug_names = 0;\n')
        (recipe / 'patches/0001-fix.patch').write_bytes(self.git('diff', '--binary'))

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.source), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def test_snapshot_is_reproducible_contains_exact_patched_source_and_no_git_data(self):
        source, files = component.source_snapshot(self.source, self.root)
        self.assertEqual(component.source_snapshot(self.source, self.root), (source, files))
        self.assertEqual(source['revision'], self.revision)
        self.assertEqual(files['LICENSE'], (self.source / 'LICENSE').read_bytes())
        unpacked = subprocess.run(['zstd', '-q', '-dc'], input=files['source.tar.zst'],
                                  stdout=subprocess.PIPE, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(unpacked)) as archive:
            self.assertEqual(set(archive.getnames()), {'LICENSE', 'shader.cpp'})
            self.assertEqual(archive.extractfile('shader.cpp').read(), b'int debug_names = 0;\n')

    def test_unexpected_tracked_source_edit_is_rejected(self):
        (self.source / 'shader.cpp').write_bytes(b'int debug_names = 42;\n')
        with self.assertRaisesRegex(ValueError, 'differs from current patches'):
            component.source_snapshot(self.source, self.root)

    def test_new_patch_requires_source_to_match_it(self):
        patch = self.root / 'tools/dxvk/patches/0001-fix.patch'
        patch.write_bytes(patch.read_bytes().replace(b'+int debug_names = 0;', b'+int debug_names = 2;'))
        with self.assertRaisesRegex(ValueError, 'differs from current patches'):
            component.source_snapshot(self.source, self.root)

    def test_unexpected_untracked_or_staged_file_is_rejected(self):
        path = self.source / 'injected.h'
        path.write_bytes(b'#define UNEXPECTED 1\n')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            component.source_snapshot(self.source, self.root)
        self.git('add', str(path))
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            component.source_snapshot(self.source, self.root)

    def test_wrong_source_head_is_rejected(self):
        self.git('add', 'shader.cpp')
        self.git('commit', '-qm', 'wrong revision')
        with self.assertRaisesRegex(ValueError, 'HEAD differs'):
            component.source_snapshot(self.source, self.root)

    def test_changed_submodule_content_and_revision_are_rejected(self):
        child = self.root / 'child'
        child.mkdir()
        subprocess.run(['git', 'init', '-q', str(child)], check=True)
        for key, value in [('user.name', 'Fixture'), ('user.email', 'fixture@example.invalid')]:
            subprocess.run(['git', '-C', str(child), 'config', key, value], check=True)
        (child / 'compiler.cpp').write_bytes(b'int compiler = 1;\n')
        subprocess.run(['git', '-C', str(child), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(child), 'commit', '-qm', 'compiler source'], check=True)
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(child), 'compiler')
        self.git('commit', '-qm', 'pin compiler')
        self.revision = self.git('rev-parse', 'HEAD').decode().strip()
        (self.root / 'tools/dxvk/source.env').write_text('DXVK_REVISION=' + self.revision + '\n')
        source, files = component.source_snapshot(self.source, self.root)
        self.assertIn(' compiler\n', source['submodules'])
        self.assertEqual(files['submodules.txt'], source['submodules'].encode())
        (self.source / 'compiler/compiler.cpp').write_bytes(b'int compiler = 2;\n')
        with self.assertRaisesRegex(ValueError, 'differs from current patches'):
            component.source_snapshot(self.source, self.root)
        subprocess.run(['git', '-C', str(self.source / 'compiler'), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qam', 'changed compiler'], check=True)
        with self.assertRaisesRegex(ValueError, 'submodule revision mismatch'):
            component.source_snapshot(self.source, self.root)


if __name__ == '__main__':
    unittest.main()
