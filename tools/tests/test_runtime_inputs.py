import importlib.util
import gzip
import io
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from unittest import mock
from zipfile import ZipFile


spec = importlib.util.spec_from_file_location('runtime_inputs', Path(__file__).resolve().parents[1] / 'release/runtime_inputs.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
profile_spec = importlib.util.spec_from_file_location('pack_profile', runtime.ROOT / 'tools/packs/profile.py')
profile = importlib.util.module_from_spec(profile_spec)
profile_spec.loader.exec_module(profile)


class RuntimeInputsTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'DROIDDECK_STEAM_ONLY': 'false'})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Component archive validation has its own binary/archive fixtures.
        component_check = patch.object(runtime, 'verify_powervr')
        component_check.start()
        self.addCleanup(component_check.stop)
        paths = runtime.outputs(self.root)
        for name in runtime.required():
            path = self.root / paths.get(name, 'app/src/main/' + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(b'inventory') if path.suffix == '.gzip' else b'prepared runtime')
        self.source = self.root / 'tools/linuxfs/preload/test.c'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('current source')
        self.audio(['pactl', 'modules/arm64/module-aaudio-sink.so', 'modules/arm64/module-directaudio-native-sink.so'])
        self.packs()
        profile.record(self.root / runtime.PACKS, 'all-stores')

    def pack(self, name, files):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for rel, content in files.items():
                entry = tarfile.TarInfo(rel)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
        path = self.root / runtime.PACKS / (name + '.tar.zst')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.run(['zstd', '-q', '-c'], input=stream.getvalue(), capture_output=True, check=True).stdout)

    def packs(self):
        self.pack('tracking', {library.rsplit('/', 1)[1]: b'library' for library in runtime.PACKED_LIBRARIES})
        proot = {}
        for name, (pack, rel) in runtime.PACKED_FILES.items():
            proot[rel] = (self.root / runtime.outputs(self.root)[name]).read_bytes()
        self.pack('proot', proot)
        self.pack('butler', {'butler': b'butler', '7z.so': b'7z', 'libc7zip.so': b'c7zip', 'version': b'15.32.0'})
        for prefix, name in runtime.PACKED.items():
            base = self.root / 'app/src/main' / prefix
            self.pack(name, {path.relative_to(base).as_posix(): path.read_bytes() for path in base.rglob('*') if path.is_file()})

    @staticmethod
    def in_apk(name):
        return name not in runtime.PACKED_FILES and not any(name.startswith(prefix) for prefix in runtime.PACKED)

    def audio(self, names):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name in names:
                entry = tarfile.TarInfo('./' + name)
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b'x'))
        path = self.root / 'app/build/prepared-assets/pulseaudio.tzst'
        path.write_bytes(subprocess.run(['zstd', '-q', '-c'], input=stream.getvalue(), capture_output=True, check=True).stdout)

    def test_an_apk_still_carrying_proot_is_rejected(self):
        apk = self.current_apk(self.root / 'bundled.apk', {'lib/arm64-v8a/libproot.so': b'prepared runtime'})
        with self.assertRaisesRegex(ValueError, 'proot pack provides'):
            runtime.check_apk(apk, self.root)

    def test_missing_proot_cannot_be_attested(self):
        (self.root / 'app/src/main/jniLibs/arm64-v8a/libproot.so').unlink()
        with self.assertRaisesRegex(ValueError, 'libproot.so'):
            runtime.record(self.root)

    def test_base_audio_without_compiled_sinks_is_rejected(self):
        self.audio(['pactl'])
        with self.assertRaisesRegex(ValueError, 'module-aaudio-sink'):
            runtime.record(self.root)

    def test_changed_native_source_rejects_old_outputs(self):
        runtime.record(self.root)
        self.source.write_text('new implementation')
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            runtime.verify(self.root)

    def test_dev_loop_can_reuse_assets_from_other_sources(self):
        runtime.record(self.root)
        self.source.write_text('new implementation')
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            runtime.verify(self.root, stale_sources_ok=True)
        self.assertIn('tools/linuxfs/preload/test.c', stderr.getvalue())

    def test_dev_loop_still_rejects_changed_outputs(self):
        runtime.record(self.root)
        (self.root / 'app/src/main/jniLibs/arm64-v8a/libproot.so').write_bytes(b'old APK binary')
        with self.assertRaisesRegex(ValueError, 'changed runtime input'):
            runtime.verify(self.root, stale_sources_ok=True)

    def test_changed_quest_source_rejects_old_outputs(self):
        source = self.root / 'tools/quest/kgsl_compat.c'
        source.parent.mkdir(parents=True)
        source.write_text('old compatibility code')
        runtime.record(self.root)
        source.write_text('new compatibility code')
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            runtime.verify(self.root)

    def test_missing_quest_driver_cannot_be_attested(self):
        (self.root / 'app/build/prepared-assets/graphics_driver/adrenotools-turnip25.1.0-quest1.tzst').unlink()
        with self.assertRaisesRegex(ValueError, 'quest1.tzst'):
            runtime.record(self.root)

    def test_changed_powervr_wrapper_rejects_old_outputs(self):
        wrapper = self.root / 'tools/hybris/run-proton.py'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text('current wrapper')
        runtime.record(self.root)
        wrapper.write_text('changed wrapper')
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            runtime.verify(self.root)

    def test_missing_powervr_bundle_cannot_be_attested(self):
        (self.root / 'app/src/main/assets/hybris/runtime.tzst').unlink()
        with self.assertRaisesRegex(ValueError, 'assets/hybris/runtime.tzst'):
            runtime.record(self.root)

    def test_modified_prebuilt_binary_is_rejected(self):
        runtime.record(self.root)
        (self.root / 'app/src/main/jniLibs/arm64-v8a/libproot.so').write_bytes(b'old APK binary')
        with self.assertRaisesRegex(ValueError, 'changed runtime input'):
            runtime.verify(self.root)

    def test_an_old_apk_is_rejected_even_when_staging_is_current(self):
        runtime.record(self.root)
        data = runtime.verify(self.root)
        apk = self.root / 'old.apk'
        with ZipFile(apk, 'w') as package:
            for name, output in data['outputs'].items():
                if not self.in_apk(name):
                    continue
                content = b'stale' if name == 'assets/linuxfs/libssbs.so' else (self.root / output['path']).read_bytes()
                package.writestr(name, content)
        with self.assertRaisesRegex(ValueError, 'stale runtime input'):
            runtime.check_apk(apk, self.root)

    def test_complete_current_payload_passes(self):
        runtime.record(self.root)
        data = runtime.verify(self.root)
        apk = self.root / 'current.apk'
        with ZipFile(apk, 'w') as package:
            for name, output in data['outputs'].items():
                if self.in_apk(name):
                    package.writestr(name, (self.root / output['path']).read_bytes())
        runtime.check_apk(apk, self.root)

    def current_apk(self, path, extra=None):
        runtime.record(self.root)
        data = runtime.verify(self.root)
        with ZipFile(path, 'w') as package:
            for name, output in data['outputs'].items():
                if self.in_apk(name):
                    package.writestr(name, (self.root / output['path']).read_bytes())
            for name, content in (extra or {}).items():
                package.writestr(name, content)
        return path

    def test_an_apk_still_carrying_a_packed_tree_is_rejected(self):
        apk = self.current_apk(self.root / 'bundled.apk', {'assets/linuxfs/usr/local/lib/droiddeck-xbox/xodus-cli': b'prepared runtime'})
        with self.assertRaisesRegex(ValueError, 'xbox pack provides'):
            runtime.check_apk(apk, self.root)

    def test_an_apk_still_carrying_the_powervr_runtime_is_rejected(self):
        apk = self.current_apk(self.root / 'bundled.apk', {'assets/hybris/runtime.tzst': b'runtime'})
        with self.assertRaisesRegex(ValueError, 'powervr pack provides'):
            runtime.check_apk(apk, self.root)

    def test_an_apk_still_carrying_tracking_libraries_is_rejected(self):
        apk = self.current_apk(self.root / 'bundled.apk', {runtime.PACKED_LIBRARIES[0]: b'library'})
        with self.assertRaisesRegex(ValueError, 'tracking pack provides'):
            runtime.check_apk(apk, self.root)

    def test_a_stale_pack_is_rejected(self):
        apk = self.current_apk(self.root / 'current.apk')
        self.pack('comet', {'comet': b'old build', 'version': b'old'})
        with self.assertRaisesRegex(ValueError, 'checksums or sizes'):
            runtime.check_apk(apk, self.root)

    def test_a_missing_pack_is_rejected(self):
        apk = self.current_apk(self.root / 'current.apk')
        (self.root / runtime.PACKS / 'xbox.tar.zst').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing add-on pack xbox'):
            runtime.check_apk(apk, self.root)

    def test_windows_manifest_matches_portable_apk_paths(self):
        path_type = type(self.root)
        original_relative_to = path_type.relative_to

        def windows_relative_to(path, *args, **kwargs):
            return PureWindowsPath(original_relative_to(path, *args, **kwargs))

        with mock.patch.object(path_type, 'relative_to', windows_relative_to):
            runtime.record(self.root)
        data = runtime.verify(self.root)
        self.assertIn('tools/linuxfs/preload/test.c', data['sources'])
        nested_asset = 'assets/linuxfs/usr/local/bin/gamescope'
        self.assertEqual(data['outputs'][nested_asset]['path'], 'app/src/main/' + nested_asset)
        for name in data['sources']:
            self.assertNotIn('\\', name)
        apk = self.root / 'portable.apk'
        with ZipFile(apk, 'w') as package:
            for name, output in data['outputs'].items():
                if not self.in_apk(name):
                    continue
                self.assertNotIn('\\', name)
                self.assertNotIn('\\', output['path'])
                package.writestr(name, (self.root / output['path']).read_bytes())
        runtime.check_apk(apk, self.root)

    def test_android_expanding_or_renaming_a_compressed_inventory_is_rejected(self):
        runtime.record(self.root)
        data = runtime.verify(self.root)
        apk = self.root / 'expanded.apk'
        with ZipFile(apk, 'w') as package:
            for name, output in data['outputs'].items():
                if not self.in_apk(name):
                    continue
                content = (self.root / output['path']).read_bytes()
                if name.endswith('.gzip'):
                    name = name.removesuffix('.gzip')
                    content = gzip.decompress(content)
                package.writestr(name, content)
        with self.assertRaisesRegex(ValueError, 'APK missing assets/desktop-removal/'):
            runtime.check_apk(apk, self.root)

    def test_malformed_removal_inventory_cannot_be_attested(self):
        path = self.root / 'app/src/main/assets/desktop-removal/kde-remove.tsv.gzip'
        path.write_bytes(b'not a gzip stream')
        with self.assertRaisesRegex(ValueError, 'Invalid desktop removal inventory'):
            runtime.record(self.root)

    def steam_apk(self, extra=None, embedded_packs=False):
        for name in runtime.STORE_PACKS:
            (self.root / runtime.PACKS / (name + '.tar.zst')).unlink(missing_ok=True)
        profile.record(self.root / runtime.PACKS, 'steam-only')
        runtime.record(self.root, steam_only=True)
        data = runtime.verify(self.root, steam_only=True)
        apk = self.root / 'steam.apk'
        with ZipFile(apk, 'w') as package:
            for name, output in data['outputs'].items():
                if self.in_apk(name):
                    package.writestr(name, (self.root / output['path']).read_bytes())
            if embedded_packs:
                for name in runtime.pack_names(True):
                    package.writestr(f'assets/packs/{name}.tar.zst',
                                     (self.root / runtime.PACKS / (name + '.tar.zst')).read_bytes())
            for name, content in (extra or {}).items():
                package.writestr(name, content)
        return apk

    def test_steam_profile_passes_without_store_runtimes_or_packs(self):
        for prefix in runtime.STORE_TREES:
            shutil.rmtree(self.root / 'app/src/main' / prefix)
        for name in runtime.STORE_PACKS:
            (self.root / runtime.PACKS / (name + '.tar.zst')).unlink()
        apk = self.steam_apk(embedded_packs=True)
        runtime.check_apk(apk, self.root, steam_only=True)
        data = runtime.verify(self.root, steam_only=True)
        self.assertEqual(set(data['packs']), {'proot', 'directaudio', 'tracking', 'powervr'})
        self.assertFalse(any(name.startswith(runtime.STORE_TREES) for name in data['outputs']))

    def test_runtime_profile_switch_requires_preparation_even_with_stale_override(self):
        runtime.record(self.root, steam_only=False)
        with self.assertRaisesRegex(ValueError, 'build profile changed'):
            runtime.verify(self.root, stale_sources_ok=True, steam_only=True)
        self.steam_apk()
        with self.assertRaisesRegex(ValueError, 'build profile changed'):
            runtime.verify(self.root, stale_sources_ok=True, steam_only=False)

    def test_steam_apk_rejects_cached_store_payloads(self):
        forbidden = [f'assets/packs/{name}.tar.zst' for name in runtime.STORE_PACKS]
        forbidden += [prefix + 'cached-binary' for prefix in runtime.STORE_TREES]
        forbidden += ['assets/xcloud/player.js', 'lib/arm64-v8a/libdroiddeckstores.so']
        for name in forbidden:
            with self.subTest(name=name):
                apk = self.steam_apk({name: b'cached payload'})
                with self.assertRaisesRegex(ValueError, 'non-Steam payload'):
                    runtime.check_apk(apk, self.root, steam_only=True)

    def test_steam_profile_ignores_unshipped_store_source_changes(self):
        files = [self.root / 'tools' / name / 'build.sh' for name in ('xbox', 'comet')]
        files.append(self.root / 'tools/packs/butler/release.env')
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('old source')
        self.steam_apk()
        for path in files:
            path.write_text('new source')
        runtime.verify(self.root, steam_only=True)
        self.source.write_text('changed shared runtime')
        with self.assertRaisesRegex(ValueError, 'sources changed'):
            runtime.verify(self.root, steam_only=True)

    def test_embedded_pack_set_must_be_complete(self):
        apk = self.steam_apk({'assets/packs/proot.tar.zst':
                             (self.root / runtime.PACKS / 'proot.tar.zst').read_bytes()})
        with self.assertRaisesRegex(ValueError, 'pack set'):
            runtime.check_apk(apk, self.root, steam_only=True)

    def test_embedded_pack_bytes_must_match_verified_pack(self):
        apk = self.steam_apk(embedded_packs=True)
        with ZipFile(apk) as package:
            contents = {name: package.read(name) for name in package.namelist()}
        contents['assets/packs/tracking.tar.zst'] = b'old tracking pack'
        with ZipFile(apk, 'w') as package:
            for name, content in contents.items():
                package.writestr(name, content)
        with self.assertRaisesRegex(ValueError, 'stale add-on pack: tracking'):
            runtime.check_apk(apk, self.root, steam_only=True)

    def test_steam_profile_still_requires_powervr_payload(self):
        (self.root / 'app/src/main/assets/hybris/runtime.tzst').unlink()
        with self.assertRaisesRegex(ValueError, 'assets/hybris/runtime.tzst'):
            runtime.record(self.root, steam_only=True)
