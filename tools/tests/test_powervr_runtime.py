import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo


spec = importlib.util.spec_from_file_location('powervr_fetch',
        Path(__file__).resolve().parents[1] / 'powervr/fetch.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def elf():
    content = bytearray(64)
    content[:6], content[18:20] = b'\x7fELF\x02\x01', b'\xb7\x00'
    return bytes(content)


def pe(architecture):
    content = bytearray(128)
    content[:2], content[60:64], content[64:68] = b'MZ', (64).to_bytes(4, 'little'), b'PE\0\0'
    content[68:70] = b'\x4c\x01' if architecture == 'x32' else b'\x64\x86'
    content[88:90] = b'\x0b\x01' if architecture == 'x32' else b'\x0b\x02'
    return bytes(content)


def tar(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, kind, content in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = content
                archive.addfile(info)
            elif kind == tarfile.REGTYPE:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                archive.addfile(info)
    return subprocess.run(['zstd', '-q', '-c'], input=stream.getvalue(),
                          capture_output=True, check=True).stdout


def tar_gz(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, kind, content in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = content
                archive.addfile(info)
            elif kind == tarfile.REGTYPE:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            else:
                archive.addfile(info)
    return stream.getvalue()


def upstream_dxvk(version, architecture_of=lambda arch: arch):
    return {'dxvk-%s/%s/%s' % (version, arch, name): pe(architecture_of(arch))
            for arch in ('x32', 'x64')
            for name in ('d3d8.dll', 'd3d9.dll', 'd3d10core.dll', 'd3d11.dll', 'dxgi.dll')}


class PowerVRRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        for name, content in {
            'tools/powervr/fetch.py': b'fixture assembler',
            'tools/hybris/run-proton.py': b'print("fixture Proton wrapper")\n',
            'tools/hybris/source.env': b'HYBRIS_REVISION=fixture\n',
            'tools/vulkan-wsi/licenses/Vulkan-Headers/LICENSE.md': b'Khronos headers attribution\n',
            'tools/vulkan-wsi/licenses/Vulkan-Headers/MIT.txt': b'MIT license fixture\n',
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        bridge = {name: ('file', 0o755, elf()) for name in runtime.REQUIRED_LIBRARIES if name.startswith('lib/')}
        bridge['lib/libhybris-common.so.1.0.0'] = bridge.pop('lib/libhybris-common.so.1')
        bridge['lib/libhybris-common.so.1'] = ('symlink', 0o777, 'libhybris-common.so.1.0.0')
        data = runtime.pack(bridge)
        self.release = {'format': 1, 'hybris': self.component('bridge.tzst', data), 'archives': []}
        self.release['hybris'].update({
            'tree_sha256': runtime.tree_digest(bridge),
            'recipe_inputs': {'tools/hybris/source.env': runtime.sha256((self.root / 'tools/hybris/source.env').read_bytes())}})
        files = {'dxvk/' + arch + '/' + name: pe(arch)
                 for arch in ('x32', 'x64')
                 for name in ('d3d8.dll', 'd3d9.dll', 'd3d10core.dll', 'd3d11.dll', 'dxgi.dll')}
        files['wsi/libVkLayer_window_system_integration.so'] = elf()
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        component = self.component('graphics.zip', stream.getvalue())
        component.update({'ignored': [], 'files': [
            {'name': name, 'path': name, 'size': len(content), 'sha256': runtime.sha256(content), 'mode': 0o644}
            for name, content in files.items()]})
        self.release['archives'].append(component)
        self.pins = self.root / 'tools/powervr/release.json'
        self.pins.write_bytes(runtime.json_bytes(self.release))

    def component(self, name, data):
        component = {'url': 'https://example.invalid/' + name, 'size': len(data),
                     'sha256': runtime.sha256(data), 'recipe_inputs': {}}
        (self.cache / (component['sha256'] + '-' + name)).write_bytes(data)
        return component

    def add_upstream_dxvk(self, files):
        top = next(iter(files)).split('/')[0]
        data = tar_gz([(top, tarfile.DIRTYPE, None)] +
                      [(name, tarfile.REGTYPE, content) for name, content in files.items()])
        component = self.component('dxvk-upstream.tar.gz', data)
        component.update({'format': 'tar.gz', 'ignored': [], 'files': [
            {'name': name, 'path': name, 'size': len(content), 'sha256': runtime.sha256(content), 'mode': 0o755}
            for name, content in files.items()]})
        self.release['archives'].append(component)
        self.pins.write_bytes(runtime.json_bytes(self.release))
        return component

    def assembled(self):
        runtime.assemble(self.root, self.cache)
        return runtime.read_tar((self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())

    def test_complete_payload_preserves_symlinks_and_is_reproducible(self):
        self.assembled()
        first = (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes()
        runtime.verify(self.root)
        self.assembled()
        self.assertEqual(first, (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        self.assertEqual(runtime.read_tar(first)['lib/libhybris-common.so.1'][0], 'symlink')

    def test_upstream_tarball_adds_a_second_dxvk_payload(self):
        files = upstream_dxvk('2.6.2')
        self.add_upstream_dxvk(files)
        entries = self.assembled()
        runtime.verify(self.root)
        for name, content in files.items():
            self.assertEqual(entries[name], ('file', 0o755, content))
        self.assertIn('dxvk/x64/d3d9.dll', entries)

    def test_second_dxvk_payload_with_wrong_pe_architecture_is_rejected(self):
        self.add_upstream_dxvk(upstream_dxvk('2.6.2', lambda arch: 'x64' if arch == 'x32' else 'x32'))
        with self.assertRaisesRegex(ValueError, 'wrong PE architecture: dxvk-2.6.2/'):
            self.assembled()

    def test_tar_gz_rejects_symlinks_unknown_members_and_changed_files(self):
        pin = {'name': 'dxvk-1/x64/d3d9.dll', 'path': 'dxvk-1/x64/d3d9.dll', 'size': 4,
               'sha256': runtime.sha256(b'good'), 'mode': 0o755}
        component = {'files': [pin], 'ignored': []}
        cases = [
            [('dxvk-1/x64/d3d9.dll', tarfile.SYMTYPE, '../../outside')],
            [('dxvk-1/x64/d3d9.dll', tarfile.REGTYPE, b'good'), ('dxvk-1/extra.dll', tarfile.REGTYPE, b'x')],
            [('dxvk-1/x64/d3d9.dll', tarfile.REGTYPE, b'evil')],
            [],
        ]
        for entries in cases:
            with self.subTest(entries=[name for name, _, _ in entries]), self.assertRaises(ValueError):
                runtime.read_tar_gz(tar_gz(entries), component)
        self.assertEqual(runtime.read_tar_gz(tar_gz([('dxvk-1/x64/d3d9.dll', tarfile.REGTYPE, b'good')]), component),
                         {'dxvk-1/x64/d3d9.dll': ('file', 0o755, b'good')})
        with self.assertRaisesRegex(ValueError, 'unsupported component archive format'):
            runtime.read_component(b'', {'format': 'rar'})

    def test_stale_wrapper_cannot_pass_with_an_updated_archive_checksum(self):
        self.assembled()
        (self.root / 'tools/hybris/run-proton.py').write_text('print("new wrapper")\n')
        with self.assertRaisesRegex(ValueError, 'assembly inputs changed'):
            runtime.verify(self.root)

    def test_header_licenses_are_bundled_verbatim(self):
        entries = self.assembled()
        for source, target in runtime.BUNDLED_LICENSES.items():
            self.assertEqual(runtime.read(entries, target), (self.root / source).read_bytes())

    def test_missing_or_modified_header_license_is_rejected(self):
        original = self.assembled()
        target = 'licenses/Vulkan-Headers/MIT.txt'
        for replacement in (None, ('file', 0o644, b'stale license')):
            with self.subTest(replacement=replacement):
                entries = dict(original)
                if replacement is None:
                    del entries[target]
                else:
                    entries[target] = replacement
                with self.assertRaisesRegex(ValueError, 'license missing or stale'):
                    runtime.check_bundle(entries, self.release, self.root)

    def test_changed_tracked_header_license_rejects_old_bundle(self):
        self.assembled()
        source = next(iter(runtime.BUNDLED_LICENSES))
        (self.root / source).write_bytes(b'changed license text')
        with self.assertRaisesRegex(ValueError, 'assembly inputs changed'):
            runtime.verify(self.root)

    def test_changed_component_recipe_requires_new_release_pin(self):
        self.assembled()
        (self.root / 'tools/hybris/source.env').write_text('HYBRIS_REVISION=new-source\n')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.verify(self.root)

    def test_old_dxvk_cannot_pass_by_updating_only_the_bundle_checksum(self):
        entries = self.assembled()
        entries['dxvk/x64/d3d11.dll'] = ('file', 0o644, pe('x64') + b'old DLL')
        archive = runtime.pack(entries)
        with self.assertRaisesRegex(ValueError, 'component mismatch'):
            runtime.verify_archive(archive, runtime.sha256(archive), self.root)

    def test_wrong_pe_architecture_rejected_even_with_matching_member_pin(self):
        entries = self.assembled()
        name = 'dxvk/x32/d3d11.dll'
        entries[name] = ('file', 0o644, pe('x64'))
        pin = next(item for item in self.release['archives'][0]['files'] if item['path'] == name)
        pin['sha256'] = runtime.sha256(pe('x64'))
        with self.assertRaisesRegex(ValueError, 'wrong PE architecture'):
            runtime.check_bundle(entries, self.release, self.root)

    def test_unpinned_bridge_file_is_rejected(self):
        entries = self.assembled()
        entries['lib/extra-loader.so'] = ('file', 0o755, elf())
        with self.assertRaisesRegex(ValueError, 'bridge differs'):
            runtime.check_bundle(entries, self.release, self.root)

    def test_failed_download_leaves_the_previous_payload_ready(self):
        self.assembled()
        before = (self.root / runtime.ASSETS / 'version.txt').read_bytes()
        component = self.release['archives'][0]
        (self.cache / (component['sha256'] + '-graphics.zip')).write_bytes(b'bad cache')
        with patch.object(runtime, 'urlopen', return_value=io.BytesIO(b'corrupt response')):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                runtime.assemble(self.root, self.cache)
        self.assertEqual(before, (self.root / runtime.ASSETS / 'version.txt').read_bytes())
        runtime.verify(self.root)

    def test_tar_rejects_traversal_duplicate_special_files_and_symlink_escape(self):
        cases = [
            [('../escape', tarfile.REGTYPE, b'x')],
            [('file', tarfile.REGTYPE, b'x'), ('./file', tarfile.REGTYPE, b'y')],
            [('device', tarfile.CHRTYPE, b'')],
            [('file', tarfile.LNKTYPE, 'other')],
            [('lib/link', tarfile.SYMTYPE, '../../outside')],
            [('lib/link', tarfile.SYMTYPE, '/absolute')],
            [('lib/a', tarfile.SYMTYPE, 'b'), ('lib/b', tarfile.SYMTYPE, 'a')],
            [('lib', tarfile.SYMTYPE, 'target'), ('target', tarfile.REGTYPE, b'x'),
             ('lib/child', tarfile.REGTYPE, b'x')],
        ]
        for members in cases:
            with self.subTest(members=members):
                with self.assertRaises(ValueError):
                    runtime.read_tar(tar(members))

    def test_relative_symlink_that_stays_inside_runtime_is_preserved(self):
        entries = runtime.read_tar(tar([
            ('lib/a', tarfile.REGTYPE, b'library'), ('lib/child/link', tarfile.SYMTYPE, '../a')]))
        self.assertEqual(runtime.read(entries, 'lib/child/link'), b'library')

    def test_zip_rejects_symlink_even_if_name_is_allowed(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            info = ZipInfo('allowed')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, '../outside')
        with self.assertRaisesRegex(ValueError, 'unexpected component archive entry'):
            runtime.read_zip(stream.getvalue(), {'files': [], 'ignored': ['allowed']})

    def test_zip_rejects_unknown_and_duplicate_members(self):
        for names in (['unexpected'], ['allowed', 'allowed'], ['../escape']):
            with self.subTest(names=names):
                stream = io.BytesIO()
                with ZipFile(stream, 'w') as archive:
                    for name in names:
                        archive.writestr(name, b'file')
                with self.assertRaises(ValueError):
                    runtime.read_zip(stream.getvalue(), {'files': [], 'ignored': ['allowed']})

    def local_component(self):
        local = runtime.local_dxvk
        source_revision = '1' * 40
        for name, data in {
            'build.sh': b'local build recipe', 'component.py': b'local component verifier',
            'source.env': ('DXVK_REVISION=' + source_revision + '\n').encode(),
            'patches/0001-fix.patch': b'local source patch',
        }.items():
            path = self.root / 'tools/dxvk' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        directory = self.root / 'local-dxvk'
        files = {name: pe(name.split('/')[0]) + b'local' for name in local.DLLS}
        submodules = ' ' + '2' * 40 + ' subprojects/compiler\n'
        files.update({'LICENSE': b'local license', 'source-commit': (source_revision + '\n').encode(),
                      'submodules.txt': submodules.encode(), 'downstream.patch': b'local source patch',
                      'source.tar.zst': b'local source archive'})
        files['SHA256SUMS'] = local.checksums(files)
        manifest = {'format': 1, 'kind': 'local-build',
                    'source': {'revision': source_revision, 'patched_tree': '3' * 40, 'submodules': submodules},
                    'recipe_inputs': local.recipe_inputs(self.root),
                    'toolchain': {name: 'fixture version 1' for name in local.TOOL_COMMANDS},
                    'files': {name: {'size': len(data), 'sha256': runtime.sha256(data)} for name, data in files.items()}}
        for name, data in {**files, local.MANIFEST: runtime.json_bytes(manifest)}.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        graphics = self.release['archives'][0]
        dxvk = dict(graphics, id='dxvk', files=[pin for pin in graphics['files'] if pin['path'].startswith('dxvk/')])
        dxvk['ignored'] = [pin['name'] for pin in graphics['files'] if not pin['path'].startswith('dxvk/')]
        dxvk['recipe_inputs'] = {'tools/dxvk/build.sh': runtime.sha256(b'old published recipe')}
        wsi = dict(graphics, id='wsi', files=[pin for pin in graphics['files'] if pin['path'].startswith('wsi/')])
        wsi['ignored'] = [pin['name'] for pin in graphics['files'] if not pin['path'].startswith('wsi/')]
        self.release['archives'] = [dxvk, wsi]
        self.pins.write_bytes(runtime.json_bytes(self.release))
        return directory, manifest

    def test_local_dxvk_is_explicit_reproducible_and_embeds_source_provenance(self):
        directory, manifest = self.local_component()
        pins_before = self.pins.read_bytes()
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.assemble(self.root, self.cache)
        with patch.object(runtime, 'urlopen', side_effect=AssertionError('no download expected')):
            runtime.assemble(self.root, self.cache, directory)
        first = (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes()
        runtime.verify(self.root)
        runtime.assemble(self.root, self.cache, directory)
        self.assertEqual(first, (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        self.assertEqual(pins_before, self.pins.read_bytes())
        entries = runtime.read_tar(first)
        self.assertEqual(runtime.embedded_local_manifest(entries, self.root), manifest)
        for source, target in runtime.local_dxvk.DESTINATIONS.items():
            self.assertEqual(runtime.read(entries, target), (directory / source).read_bytes())
        self.assertEqual(runtime.read(entries, 'wsi/libVkLayer_window_system_integration.so'), elf())

    def test_local_dxvk_does_not_bypass_other_component_pins(self):
        directory, _ = self.local_component()
        (self.root / 'tools/hybris/source.env').write_bytes(b'changed bridge recipe')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.assemble(self.root, self.cache, directory)

    def test_local_dxvk_recipe_change_rejects_existing_component_and_runtime(self):
        directory, _ = self.local_component()
        runtime.assemble(self.root, self.cache, directory)
        (self.root / 'tools/dxvk/patches/0001-fix.patch').write_bytes(b'new fix')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.assemble(self.root, self.cache, directory)
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.verify(self.root)

    def test_local_dxvk_missing_license_extra_file_symlink_and_tamper_are_rejected(self):
        directory, _ = self.local_component()
        license_path = directory / 'LICENSE'
        original = license_path.read_bytes()
        for mutation in ('missing', 'symlink', 'tamper', 'extra'):
            with self.subTest(mutation=mutation):
                if mutation == 'missing':
                    license_path.unlink()
                elif mutation == 'symlink':
                    license_path.unlink()
                    license_path.symlink_to(directory / 'source-commit')
                elif mutation == 'tamper':
                    license_path.write_bytes(b'changed')
                else:
                    (directory / 'extra.dll').write_bytes(b'unlisted')
                with self.assertRaises(ValueError):
                    runtime.assemble(self.root, self.cache, directory)
                license_path.unlink(missing_ok=True)
                license_path.write_bytes(original)
                (directory / 'extra.dll').unlink(missing_ok=True)

    def test_local_dxvk_wrong_pe_architecture_rejected_even_with_updated_manifest(self):
        directory, manifest = self.local_component()
        local = runtime.local_dxvk
        name = 'x32/d3d11.dll'
        (directory / name).write_bytes(pe('x64'))
        files = {path: (directory / path).read_bytes() for path in local.DESTINATIONS}
        files['SHA256SUMS'] = local.checksums(files)
        (directory / 'SHA256SUMS').write_bytes(files['SHA256SUMS'])
        for path in (name, 'SHA256SUMS'):
            manifest['files'][path] = {'size': len(files[path]), 'sha256': runtime.sha256(files[path])}
        (directory / local.MANIFEST).write_bytes(runtime.json_bytes(manifest))
        with self.assertRaisesRegex(ValueError, 'wrong PE architecture'):
            runtime.assemble(self.root, self.cache, directory)

    def test_local_dxvk_payload_tamper_rejected_with_updated_outer_checksum(self):
        directory, _ = self.local_component()
        runtime.assemble(self.root, self.cache, directory)
        entries = runtime.read_tar((self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        entries['dxvk/x64/d3d11.dll'] = ('file', 0o755, pe('x64') + b'changed')
        archive = runtime.pack(entries)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            runtime.verify_archive(archive, runtime.sha256(archive), self.root)

    def local_hybris_component(self):
        local = runtime.local_hybris
        revision, headers = '4' * 40, '5' * 40
        recipes = {
            'component.py': b'local hybris verifier', 'build_layer.py': b'local layer build',
            'build_runtime.sh': b'pinned full bridge recipe',
            'source.env': ('HYBRIS_REVISION=' + revision + '\n').encode(),
            'layer-source.env': ('VULKAN_HEADERS_REVISION=' + headers + '\n').encode(),
            'powervr-hal-version.patch': b'pinned HAL patch',
            'patches/0001-profile.patch': b'pinned BC profile',
        }
        for name, content in recipes.items():
            path = self.root / 'tools/hybris' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.release['hybris']['recipe_inputs'] = {
            'tools/hybris/' + name: runtime.sha256(recipes[name])
            for name in ('source.env', 'build_runtime.sh')}
        self.pins.write_bytes(runtime.json_bytes(self.release))
        library = bytearray(elf())
        library[16:18] = b'\x03\x00'
        build = {'format': 1, 'scope': 'compat-layer-only',
                 'toolchain': dict(compiler='fixture compiler', target='aarch64-linux-gnu',
                                   linker='fixture linker', strip='fixture strip', zstd='fixture zstd'),
                 'sources': {'compat/bc_transfer.c': '6' * 64},
                 'compile_flags': ['-O2'], 'link_flags': ['-shared']}
        files = {name: ('local provenance: ' + name).encode() for name in local.DESTINATIONS}
        files.update({local.LAYER: bytes(library) + b'local layer',
                      'source-commit': (revision + '\n').encode(),
                      'headers-source-commit': (headers + '\n').encode(),
                      'build-info.json': runtime.json_bytes(build)})
        files['SHA256SUMS'] = local.checksums(files)
        manifest = {'format': 1, 'kind': 'local-compat-layer',
                    'base': local.base_identity(self.root),
                    'source': {'revision': revision, 'patched_tree': '7' * 40},
                    'headers': {'revision': headers, 'patched_tree': '8' * 40},
                    'recipe_inputs': local.recipe_inputs(self.root),
                    'files': {name: {'size': len(data), 'sha256': runtime.sha256(data)}
                              for name, data in files.items()}}
        directory = self.root / 'local-hybris'
        directory.mkdir()
        for name, data in {**files, local.MANIFEST: runtime.json_bytes(manifest)}.items():
            (directory / name).write_bytes(data)
        return directory, manifest

    def test_local_hybris_replaces_only_layer_and_preserves_pinned_base(self):
        directory, manifest = self.local_hybris_component()
        pins_before = self.pins.read_bytes()
        original = self.assembled()
        pinned_archive = (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes()
        with patch.object(runtime, 'urlopen', side_effect=AssertionError('no download expected')):
            runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        archive = (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes()
        entries = runtime.read_tar(archive)
        runtime.verify(self.root)
        self.assertEqual(entries[runtime.HYBRIS_BASE_LAYER], original[runtime.HYBRIS_LAYER])
        self.assertEqual(runtime.embedded_local_manifest(entries, self.root, runtime.local_hybris), manifest)
        for source, target in runtime.local_hybris.DESTINATIONS.items():
            self.assertEqual(runtime.read(entries, target), (directory / source).read_bytes())
        for name, entry in original.items():
            if name not in (runtime.HYBRIS_LAYER, 'assembly-inputs.json'):
                self.assertEqual(entries[name], entry)
        runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        self.assertEqual(archive, (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        runtime.assemble(self.root, self.cache)
        self.assertEqual(pinned_archive, (self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        self.assertEqual(pins_before, self.pins.read_bytes())

    def test_local_hybris_still_verifies_every_original_bridge_entry(self):
        directory, _ = self.local_hybris_component()
        runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        original = runtime.read_tar((self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        cases = [
            (runtime.HYBRIS_BASE_LAYER, ('file', 0o755, elf() + b'changed original')),
            (runtime.HYBRIS_BASE_LAYER, ('file', 0o644, elf())),
            ('lib/libhybris-common.so.1.0.0', ('file', 0o755, elf() + b'changed bridge')),
            ('lib/libhybris-common.so.1', ('file', 0o755, elf())),
            ('lib/unpinned.so', ('file', 0o755, elf())),
        ]
        for name, entry in cases:
            with self.subTest(name=name, kind=entry[0]):
                entries = dict(original, **{name: entry})
                archive = runtime.pack(entries)
                with self.assertRaisesRegex(ValueError, 'bridge differs'):
                    runtime.verify_archive(archive, runtime.sha256(archive), self.root)
        del original[runtime.HYBRIS_BASE_LAYER]
        archive = runtime.pack(original)
        with self.assertRaisesRegex(ValueError, 'missing original pinned'):
            runtime.verify_archive(archive, runtime.sha256(archive), self.root)

    def test_local_hybris_payload_and_provenance_tamper_are_rejected(self):
        directory, _ = self.local_hybris_component()
        runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        original = runtime.read_tar((self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        for name in (runtime.HYBRIS_LAYER, 'licenses/hybris-local/source.tar.zst'):
            with self.subTest(name=name):
                entries = dict(original)
                kind, mode, data = entries[name]
                entries[name] = (kind, mode, data + b'tampered')
                archive = runtime.pack(entries)
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    runtime.verify_archive(archive, runtime.sha256(archive), self.root)
        entries = dict(original)
        entries[runtime.HYBRIS_LAYER] = ('file', 0o644, runtime.read(entries, runtime.HYBRIS_LAYER))
        archive = runtime.pack(entries)
        with self.assertRaisesRegex(ValueError, 'mode or type mismatch'):
            runtime.verify_archive(archive, runtime.sha256(archive), self.root)

    def test_local_hybris_does_not_bypass_base_recipe_pins(self):
        directory, _ = self.local_hybris_component()
        runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        (self.root / 'tools/hybris/build_runtime.sh').write_bytes(b'changed full bridge recipe')
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.assemble(self.root, self.cache, local_hybris_component=directory)
        with self.assertRaisesRegex(ValueError, 'recipe changed'):
            runtime.verify(self.root)

    def test_local_hybris_and_dxvk_can_be_selected_together(self):
        dxvk, dxvk_manifest = self.local_component()
        hybris, hybris_manifest = self.local_hybris_component()
        pins_before = self.pins.read_bytes()
        runtime.assemble(self.root, self.cache, dxvk, hybris)
        runtime.verify(self.root)
        entries = runtime.read_tar((self.root / runtime.ASSETS / 'runtime.tzst').read_bytes())
        self.assertEqual(json.loads(runtime.read(entries, 'assembly-inputs.json'))['local_components'], {
            'dxvk': runtime.sha256(runtime.json_bytes(dxvk_manifest)),
            'hybris': runtime.sha256(runtime.json_bytes(hybris_manifest)),
        })
        self.assertEqual(pins_before, self.pins.read_bytes())
        entries['lib/libhybris-common.so.1.0.0'] = ('file', 0o755, elf() + b'tampered')
        archive = runtime.pack(entries)
        with self.assertRaisesRegex(ValueError, 'bridge differs'):
            runtime.verify_archive(archive, runtime.sha256(archive), self.root)


if __name__ == '__main__':
    unittest.main()
