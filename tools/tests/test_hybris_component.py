import copy
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('hybris_component',
        Path(__file__).resolve().parents[1] / 'hybris/component.py')
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


class HybrisComponentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.headers = self.root / 'source', self.root / 'headers'
        for directory in (self.source, self.headers):
            directory.mkdir()
            self.git(directory, 'init', '-q')
            self.git(directory, 'config', 'user.name', 'Fixture')
            self.git(directory, 'config', 'user.email', 'fixture@example.invalid')
        for name in component.LICENSES:
            (self.source / name).write_text(name + '\n')
        (self.source / 'shader.c').write_text('int value = 0;\n')
        (self.source / 'links').mkdir()
        (self.source / 'links/internal.h').symlink_to('../shader.c')
        (self.headers / 'vulkan.h').write_text('/* pinned headers */\n')
        for directory in (self.source, self.headers):
            self.git(directory, 'add', '.')
            self.git(directory, 'commit', '-qm', 'pinned fixture')
        self.revision = self.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        self.header_revision = self.git(self.headers, 'rev-parse', 'HEAD').decode().strip()
        recipe = self.root / 'tools/hybris'
        (recipe / 'patches').mkdir(parents=True)
        for name in ('component.py', 'build_layer.py'):
            (recipe / name).write_text('fixture recipe\n')
        (recipe / 'source.env').write_text('HYBRIS_REVISION=' + self.revision + '\n')
        (recipe / 'layer-source.env').write_text('VULKAN_HEADERS_REVISION=' + self.header_revision + '\n')
        (recipe / 'powervr-hal-version.patch').write_bytes(self.patch(0, 1))
        (recipe / 'patches/0001.patch').write_bytes(self.patch(1, 2))
        self.local_patch = self.patch(2, 3)
        (self.source / 'shader.c').write_text('int value = 3;\n')
        release = self.root / 'tools/powervr/release.json'
        release.parent.mkdir()
        release.write_bytes(component.json_bytes({'hybris': {'sha256': 'a' * 64, 'tree_sha256': 'b' * 64}}))

    @staticmethod
    def patch(before, after):
        return (f'--- a/shader.c\n+++ b/shader.c\n@@ -1 +1 @@\n-int value = {before};\n'
                f'+int value = {after};\n').encode()

    @staticmethod
    def git(directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def snapshot(self):
        return component.source_snapshot(self.source, self.headers, self.local_patch, self.root)

    def fixture(self):
        source, headers, files = self.snapshot()
        elf = bytearray(64)
        elf[:6], elf[16:18], elf[18:20] = b'\x7fELF\x02\x01', b'\x03\x00', b'\xb7\x00'
        files[component.LAYER] = bytes(elf)
        files['build-info.json'] = component.json_bytes({'format': 1, 'scope': 'compat-layer-only',
            'toolchain': dict(compiler='gcc fixture', target='aarch64-linux-gnu', linker='ld fixture',
                              strip='strip fixture', zstd='zstd fixture'),
            'sources': {'shader.c': component.sha256((self.source / 'shader.c').read_bytes())},
            'compile_flags': ['-O2'], 'link_flags': ['-shared']})
        files['SHA256SUMS'] = component.checksums(files)
        manifest = {'format': 1, 'kind': 'local-compat-layer', 'base': component.base_identity(self.root),
            'source': source, 'headers': headers, 'recipe_inputs': component.recipe_inputs(self.root),
            'files': {name: {'size': len(data), 'sha256': component.sha256(data)} for name, data in files.items()}}
        return files, manifest

    def test_reproducible_full_source_includes_local_patch_and_safe_relative_symlink(self):
        result = self.snapshot()
        self.assertEqual(result, self.snapshot())
        packed = result[2]['source.tar.zst']
        data = component.run(['zstd', '-q', '-dc'], input=packed)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(archive.extractfile('shader.c').read(), b'int value = 3;\n')
            self.assertTrue(archive.getmember('links/internal.h').issym())
            self.assertEqual(archive.getmember('links/internal.h').linkname, '../shader.c')
            self.assertTrue(set(component.LICENSES).issubset(archive.getnames()))
            self.assertFalse(any(name.startswith('.git/') for name in archive.getnames()))

    def test_undeclared_source_edit_is_rejected(self):
        (self.source / 'shader.c').write_text('int value = 9;\n')
        with self.assertRaisesRegex(ValueError, 'differs from declared patches'):
            self.snapshot()

    def test_modified_headers_and_untracked_files_are_rejected(self):
        (self.headers / 'vulkan.h').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'differs from declared patches'):
            self.snapshot()
        self.git(self.headers, 'checkout', '--', 'vulkan.h')
        (self.source / 'extra.c').write_text('unexpected\n')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            self.snapshot()

    def test_wrong_source_head_and_wrong_declared_local_patch_are_rejected(self):
        self.local_patch = self.patch(2, 8)
        with self.assertRaisesRegex(ValueError, 'differs from declared patches'):
            self.snapshot()
        self.local_patch = self.patch(2, 3)
        self.git(self.source, 'commit', '-qam', 'different head')
        with self.assertRaisesRegex(ValueError, 'HEAD differs'):
            self.snapshot()

    def test_source_archive_rejects_an_escaping_symlink(self):
        link = self.source / 'links/internal.h'
        link.unlink()
        link.symlink_to('../../outside.h')
        self.git(self.source, 'commit', '-qam', 'unsafe fixture')
        head = self.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError, 'unsafe local hybris source symlink'):
            component.snapshot(self.source, head)

    def test_payload_and_base_pin_tampering_are_rejected(self):
        files, manifest = self.fixture()
        component.verify_files(files, manifest, self.root)
        changed = copy.deepcopy(manifest)
        changed['base']['tree_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'base bridge pin changed'):
            component.verify_files(files, changed, self.root)
        files[component.LAYER] += b'tampered'
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            component.verify_files(files, manifest, self.root)

    def test_wrong_architecture_and_missing_provenance_are_rejected(self):
        files, manifest = self.fixture()
        files[component.LAYER] = files[component.LAYER][:18] + b'\x3e\x00' + files[component.LAYER][20:]
        files['SHA256SUMS'] = component.checksums(files)
        for name, data in files.items():
            manifest['files'][name] = {'size': len(data), 'sha256': component.sha256(data)}
        with self.assertRaisesRegex(ValueError, 'ELF64 AArch64'):
            component.verify_files(files, manifest, self.root)
        files.pop('source.tar.zst')
        with self.assertRaisesRegex(ValueError, 'missing local hybris'):
            component.verify_files(files, manifest, self.root)

    def test_recipe_changes_require_rebuild_and_directory_symlinks_are_rejected(self):
        files, manifest = self.fixture()
        output = self.root / 'component'
        output.mkdir()
        for name, data in files.items():
            (output / name).write_bytes(data)
        (output / component.MANIFEST).write_bytes(component.json_bytes(manifest))
        component.read_directory(output, self.root)
        (output / 'extra').symlink_to('source.tar.zst')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            component.read_directory(output, self.root)
        (output / 'extra').unlink()
        (self.root / 'tools/hybris/build_layer.py').write_text('changed recipe\n')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            component.read_directory(output, self.root)


if __name__ == '__main__':
    unittest.main()
