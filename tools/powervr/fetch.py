#!/usr/bin/env python3
"""Assemble the pinned PowerVR components into the APK's isolated Linux runtime.

Like DirectAudio, APK builds download published components rather than compile
them. Their source, licenses and patches travel with the payload. Build recipes
remain in tools/{hybris,dxvk,vulkan-wsi}; release.json pins their reviewed inputs.
An explicit --local-dxvk directory replaces only DXVK with a verified local build.
An explicit --local-hybris directory replaces only its verified compatibility layer.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dxvk_component", ROOT / "tools/dxvk/component.py")
local_dxvk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local_dxvk)
_spec = importlib.util.spec_from_file_location("hybris_component", ROOT / "tools/hybris/component.py")
local_hybris = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local_hybris)
ASSETS = Path("app/src/main/assets/hybris")
HYBRIS_LAYER = "lib/libVkLayer_hybris_compat.so"
HYBRIS_BASE_LAYER = "provenance/hybris-original-compat-layer.so"
REQUIRED_LIBRARIES = (
    "lib/libhybris-vulkan-icd.so.0", "lib/libhybris-common.so.1",
    "lib/libhybris/linker/q.so", "lib/libVkLayer_hybris_compat.so",
    "wsi/libVkLayer_window_system_integration.so",
)
MAX_UNPACKED = 256 * 1024 * 1024
BUNDLED_LICENSES = {
    'tools/vulkan-wsi/licenses/Vulkan-Headers/' + name: 'licenses/Vulkan-Headers/' + name
    for name in ('LICENSE.md', 'MIT.txt')
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("unsafe archive path: " + name)
    return str(path)


def check_tree(entries):
    # Never extract through a symlink, even one pointing inside the tree.
    for name, entry in entries.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in entries:
                raise ValueError("archive member below a file or symlink: " + name)
        if entry[0] == "symlink":
            read(entries, name)


def read(entries, name, seen=()):
    if name in seen or len(seen) > 16:
        raise ValueError("runtime symlink cycle: " + name)
    if name not in entries:
        raise ValueError("missing runtime file: " + name)
    kind, mode, data = entries[name]
    if kind == "symlink":
        if PurePosixPath(data).is_absolute() or "\\" in data:
            raise ValueError("unsafe runtime symlink: " + name)
        target = safe_name(posixpath.normpath(posixpath.join(posixpath.dirname(name), data)))
        return read(entries, target, (*seen, name))
    return data


def read_tar(data):
    unpacked = subprocess.run(["zstd", "-q", "-dc"], input=data,
                              capture_output=True, check=True).stdout
    if len(unpacked) > MAX_UNPACKED:
        raise ValueError("runtime archive exceeds size limit")
    entries, seen = {}, set()
    with tarfile.open(fileobj=io.BytesIO(unpacked)) as package:
        for member in package:
            name = safe_name(member.name)
            if name in seen:
                raise ValueError("duplicate archive path: " + name)
            seen.add(name)
            if member.isdir():
                continue
            if name == ".":
                raise ValueError("archive root must be a directory")
            if member.issym():
                entries[name] = ("symlink", 0o777, member.linkname)
            elif member.isfile():
                entries[name] = ("file", 0o755 if member.mode & 0o111 else 0o644,
                                 package.extractfile(member).read())
            else:
                raise ValueError("unsupported archive entry type: " + name)
    check_tree(entries)
    return entries


def tree_digest(entries):
    return sha256(json_bytes({name: [kind, mode, sha256(data) if kind == "file" else data]
                             for name, (kind, mode, data) in sorted(entries.items())}))


def load_release(root, local_manifest=None):
    release = json.loads((root / "tools/powervr/release.json").read_bytes())
    if release.get("format") != 1:
        raise ValueError("unsupported PowerVR release pins")
    # These identify the reviewed recipe, not a new attestation from the old
    # producer. The original build metadata is preserved inside the runtime.
    for component in [release["hybris"], *release["archives"]]:
        if local_manifest is not None and component.get("id") == "dxvk":
            continue
        for name, expected in component["recipe_inputs"].items():
            if sha256((root / name).read_bytes()) != expected:
                raise ValueError("PowerVR component recipe changed; update its release pin: " + name)
    if local_manifest is not None:
        if sum(component.get("id") == "dxvk" for component in release["archives"]) != 1:
            raise ValueError("local DXVK requires exactly one default DXVK component")
        pins = [{"name": name, "path": target, "mode": 0o755 if name in local_dxvk.DLLS else 0o644,
                 **local_manifest["files"][name]} for name, target in local_dxvk.DESTINATIONS.items()]
        data = json_bytes(local_manifest)
        pins.append({"name": local_dxvk.MANIFEST, "path": local_dxvk.MANIFEST_DESTINATION,
                     "mode": 0o644, "size": len(data), "sha256": sha256(data)})
        release["archives"] = [dict(id="dxvk", kind="local-build", files=pins)
                               if component.get("id") == "dxvk" else component
                               for component in release["archives"]]
    return release


def assembly_inputs(root, local_manifest=None, hybris_manifest=None):
    result = {"format": 1, "inputs": {
        name: sha256((root / name).read_bytes()) for name in
        ("tools/powervr/release.json", "tools/powervr/fetch.py", "tools/hybris/run-proton.py", *BUNDLED_LICENSES)}}
    components = {name: sha256(json_bytes(manifest)) for name, manifest in
                  (("dxvk", local_manifest), ("hybris", hybris_manifest)) if manifest is not None}
    if components:
        result["local_components"] = components
    return result


def fetch(component, cache):
    url = component["url"]
    if urlparse(url).scheme != "https":
        raise ValueError("component URL must use HTTPS")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (component["sha256"] + "-" + PurePosixPath(urlparse(url).path).name)

    def valid(path):
        return (path.is_file() and path.stat().st_size == component["size"]
                and sha256(path.read_bytes()) == component["sha256"])

    if valid(target):
        return target.read_bytes()
    # No unverified response is ever parsed or promoted into the cache.
    with tempfile.NamedTemporaryFile(dir=cache, prefix=".download-", delete=False) as output:
        temporary = Path(output.name)
        try:
            with urlopen(url, timeout=60) as response:
                remaining = component["size"]
                while block := response.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(block)
                    if remaining < 0:
                        raise ValueError("component download exceeds pinned size")
                    output.write(block)
            output.flush()
            if not valid(temporary):
                raise ValueError("component download checksum mismatch: " + url)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.read_bytes()


def read_zip(data, component):
    entries, seen = {}, set()
    expected = {item["name"]: item for item in component["files"]}
    allowed = set(expected) | set(component["ignored"])
    total = 0
    with ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            name = safe_name(member.filename)
            if name in seen:
                raise ValueError("duplicate archive path: " + name)
            seen.add(name)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if member.is_dir() and kind in (0, stat.S_IFDIR):
                continue
            if kind not in (0, stat.S_IFREG) or name not in allowed:
                raise ValueError("unexpected component archive entry: " + name)
            total += member.file_size
            if total > MAX_UNPACKED:
                raise ValueError("component archive exceeds size limit")
            if name not in expected:
                continue
            pin = expected[name]
            content = archive.read(member)
            if len(content) != pin["size"] or sha256(content) != pin["sha256"]:
                raise ValueError("component member checksum mismatch: " + name)
            path = safe_name(pin["path"])
            if path in entries:
                raise ValueError("duplicate component destination: " + path)
            entries[path] = ("file", pin["mode"], content)
    if set(expected) - seen:
        raise ValueError("component archive missing files: " + ", ".join(sorted(set(expected) - seen)))
    check_tree(entries)
    return entries


def read_tar_gz(data, component):
    """An upstream release tarball, held to the same per-file pins as a CI archive."""
    entries, seen = {}, set()
    expected = {item["name"]: item for item in component["files"]}
    allowed = set(expected) | set(component["ignored"])
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            name = safe_name(member.name)
            if name in seen:
                raise ValueError("duplicate archive path: " + name)
            seen.add(name)
            if member.isdir():
                continue
            if not member.isfile() or name not in allowed:
                raise ValueError("unexpected component archive entry: " + name)
            total += member.size
            if total > MAX_UNPACKED:
                raise ValueError("component archive exceeds size limit")
            if name not in expected:
                continue
            pin = expected[name]
            content = archive.extractfile(member).read()
            if len(content) != pin["size"] or sha256(content) != pin["sha256"]:
                raise ValueError("component member checksum mismatch: " + name)
            path = safe_name(pin["path"])
            if path in entries:
                raise ValueError("duplicate component destination: " + path)
            entries[path] = ("file", pin["mode"], content)
    if set(expected) - seen:
        raise ValueError("component archive missing files: " + ", ".join(sorted(set(expected) - seen)))
    check_tree(entries)
    return entries


def read_component(data, component):
    kind = component.get("format", "zip")
    if kind == "zip":
        return read_zip(data, component)
    if kind == "tar.gz":
        return read_tar_gz(data, component)
    raise ValueError("unsupported component archive format: " + kind)


def embedded_local_manifest(entries, root, component=local_dxvk):
    name = component.MANIFEST_DESTINATION
    if name not in entries:
        return None
    data = read(entries, name)
    manifest = json.loads(data)
    if entries[name][:2] != ("file", 0o644) or data != json_bytes(manifest):
        raise ValueError("invalid embedded local component manifest: " + name)
    component.verify_files({name: read(entries, target) for name, target in component.DESTINATIONS.items()},
                           manifest, root)
    return manifest


def hybris_entries(files, manifest):
    additions = {target: ("file", 0o755 if target == HYBRIS_LAYER else 0o644, files[name])
                 for name, target in local_hybris.DESTINATIONS.items()}
    additions[local_hybris.MANIFEST_DESTINATION] = ("file", 0o644, json_bytes(manifest))
    return additions


def check_bundle(entries, release, root, local_manifest=None, hybris_manifest=None):
    check_tree(entries)
    if embedded_local_manifest(entries, root) != local_manifest:
        raise ValueError("unexpected local DXVK component")
    if embedded_local_manifest(entries, root, local_hybris) != hybris_manifest:
        raise ValueError("unexpected local hybris component")
    if read(entries, "assembly-inputs.json") != json_bytes(assembly_inputs(root, local_manifest, hybris_manifest)):
        raise ValueError("PowerVR assembly inputs changed; run tools/powervr/fetch.py")
    if entries["run-proton.py"] != ("file", 0o755, (root / "tools/hybris/run-proton.py").read_bytes()):
        raise ValueError("PowerVR Proton wrapper is stale or not executable")
    compile(read(entries, "run-proton.py"), "run-proton.py", "exec")
    bridge = dict(entries)
    del bridge["assembly-inputs.json"], bridge["run-proton.py"]
    for source, target in BUNDLED_LICENSES.items():
        if entries.get(target) != ("file", 0o644, (root / source).read_bytes()):
            raise ValueError("Vulkan-Headers license missing or stale: " + target)
    for component in release["archives"]:
        for pin in component["files"]:
            content = read(entries, pin["path"])
            if (entries[pin["path"]][:2] != ("file", pin["mode"])
                    or len(content) != pin["size"] or sha256(content) != pin["sha256"]):
                raise ValueError("PowerVR component mismatch: " + pin["path"])
            del bridge[pin["path"]]
    for target in BUNDLED_LICENSES.values():
        bridge.pop(target, None)
    if hybris_manifest is not None:
        files = {name: read(entries, target) for name, target in local_hybris.DESTINATIONS.items()}
        for target, expected in hybris_entries(files, hybris_manifest).items():
            if entries[target] != expected:
                raise ValueError("PowerVR local hybris component mode or type mismatch: " + target)
            del bridge[target]
        original = bridge.pop(HYBRIS_BASE_LAYER, None)
        if original is None or original[0] != "file":
            raise ValueError("missing original pinned hybris compatibility layer")
        # Restore the replaced entry before checking the entire pinned base tree.
        bridge[HYBRIS_LAYER] = original
    if tree_digest(bridge) != release["hybris"]["tree_sha256"]:
        raise ValueError("PowerVR bridge differs from the pinned archive")
    for name in REQUIRED_LIBRARIES:
        content = read(entries, name)
        if content[:6] != b"\x7fELF\x02\x01" or content[18:20] != b"\xb7\x00":
            raise ValueError("runtime library is not ELF64 AArch64: " + name)
    # dxvk/ is the default payload; run-proton.py can select another dxvk-* sibling by name.
    payloads = sorted({name.split("/")[0] for name in entries if re.fullmatch(r"dxvk[^/]*/x(32|64)/[^/]+\.dll", name)})
    if "dxvk" not in payloads:
        raise ValueError("default DXVK payload missing")
    for payload in payloads:
        for architecture, machine, magic in (("x32", b"\x4c\x01", b"\x0b\x01"),
                                             ("x64", b"\x64\x86", b"\x0b\x02")):
            for name in ("d3d8.dll", "d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll"):
                path = payload + "/" + architecture + "/" + name
                content = read(entries, path)
                pe = int.from_bytes(content[60:64], "little")
                if (content[:2] != b"MZ" or pe < 64 or content[pe:pe + 4] != b"PE\0\0"
                        or content[pe + 4:pe + 6] != machine or content[pe + 24:pe + 26] != magic):
                    raise ValueError("DXVK library has wrong PE architecture: " + path)


def pack(entries):
    stream = io.BytesIO()
    directories = {str(parent) for name in entries for parent in PurePosixPath(name).parents
                   if str(parent) != "."}
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(directories | set(entries)):
            info = tarfile.TarInfo(name)
            info.mtime = info.uid = info.gid = 0
            if name in directories:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
                continue
            kind, info.mode, data = entries[name]
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, data
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return subprocess.run(["zstd", "-q", "-10", "-T1", "-c"], input=stream.getvalue(),
                          capture_output=True, check=True).stdout


def verify_archive(archive, version, root=ROOT):
    if sha256(archive) != version.strip():
        raise ValueError("PowerVR runtime archive checksum mismatch")
    entries = read_tar(archive)
    manifest = embedded_local_manifest(entries, root)
    hybris_manifest = embedded_local_manifest(entries, root, local_hybris)
    check_bundle(entries, load_release(root, manifest), root, manifest, hybris_manifest)


def verify(root=ROOT):
    verify_archive((root / ASSETS / "runtime.tzst").read_bytes(),
                   (root / ASSETS / "version.txt").read_text(), root)


def assemble(root, cache, local_component=None, local_hybris_component=None):
    files, manifest = local_dxvk.read_directory(local_component, root) if local_component is not None else ({}, None)
    hybris_files, hybris_manifest = (local_hybris.read_directory(local_hybris_component, root)
                                    if local_hybris_component is not None else ({}, None))
    release = load_release(root, manifest)
    entries = read_tar(fetch(release["hybris"], cache))
    if tree_digest(entries) != release["hybris"]["tree_sha256"]:
        raise ValueError("PowerVR bridge tree checksum mismatch")
    for component in release["archives"]:
        if component.get("kind") == "local-build":
            payload = dict(files, **{local_dxvk.MANIFEST: json_bytes(manifest)})
            additions = {pin["path"]: ("file", pin["mode"], payload[pin["name"]]) for pin in component["files"]}
        else:
            additions = read_component(fetch(component, cache), component)
        if set(entries) & set(additions):
            raise ValueError("component overwrites existing runtime files")
        entries.update(additions)
    if hybris_manifest is not None:
        original = entries.get(HYBRIS_LAYER)
        if original is None or original[0] != "file":
            raise ValueError("pinned hybris compatibility layer must be a regular file")
        additions = hybris_entries(hybris_files, hybris_manifest)
        additions[HYBRIS_BASE_LAYER] = original
        if (set(entries) & set(additions)) - {HYBRIS_LAYER}:
            raise ValueError("local hybris component overwrites another runtime file")
        entries.update(additions)
    for source, target in BUNDLED_LICENSES.items():
        license_entry = ("file", 0o644, (root / source).read_bytes())
        if target in entries and entries[target] != license_entry:
            raise ValueError("bundled license overwrites component file: " + target)
        entries[target] = license_entry
    entries["run-proton.py"] = ("file", 0o755, (root / "tools/hybris/run-proton.py").read_bytes())
    entries["assembly-inputs.json"] = ("file", 0o644, json_bytes(assembly_inputs(root, manifest, hybris_manifest)))
    check_bundle(entries, release, root, manifest, hybris_manifest)
    archive = pack(entries)
    assets = root / ASSETS
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=assets.parent, prefix=".hybris-") as directory:
        stage = Path(directory)
        (stage / "runtime.tzst").write_bytes(archive)
        (stage / "version.txt").write_text(sha256(archive) + "\n")
        # version.txt is the readiness marker; an interrupted replacement fails
        # closed instead of presenting a partially updated payload as ready.
        (assets / "version.txt").unlink(missing_ok=True)
        os.replace(stage / "runtime.tzst", assets / "runtime.tzst")
        os.replace(stage / "version.txt", assets / "version.txt")
    selected = [name for name, value in (("DXVK", manifest), ("hybris compatibility layer", hybris_manifest)) if value]
    print("PowerVR runtime prepared" + (" (local " + ", ".join(selected) + ")" if selected else "") + ": " + sha256(archive))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--local-dxvk", type=Path, help="use a verified local tools/dxvk/build.sh output directory")
    parser.add_argument("--local-hybris", type=Path, help="use a verified local hybris compatibility-layer component")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get(
        "DROIDDECK_BUILD_CACHE", str(Path.home() / ".cache/droiddeck-build"))))
    args = parser.parse_args()
    if args.verify and (args.local_dxvk is not None or args.local_hybris is not None):
        parser.error("--verify reads embedded provenance; local component options are only for assembly")
    if args.verify:
        verify()
    else:
        assemble(ROOT, args.cache_dir, args.local_dxvk, args.local_hybris)


if __name__ == "__main__":
    main()
