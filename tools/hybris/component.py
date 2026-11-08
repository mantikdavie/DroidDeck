#!/usr/bin/env python3
"""Verify an explicit local compatibility layer while retaining the pinned bridge."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "component-manifest.json"
MANIFEST_DESTINATION = "provenance/hybris-local-component-manifest.json"
LAYER = "libVkLayer_hybris_compat.so"
LICENSES = tuple("LICENSE." + name for name in
                 ("Apache2", "BSD-2", "BSD-3", "BSD-4", "GPL3", "ISC", "LGPLv21", "MIT"))
DESTINATIONS = {LAYER: "lib/" + LAYER} | {
    "source-commit": "provenance/hybris-local-source-commit",
    "headers-source-commit": "provenance/hybris-local-headers-source-commit",
    "build-info.json": "provenance/hybris-local-build-info.json",
    "SHA256SUMS": "provenance/hybris-local-SHA256SUMS",
} | {name: "licenses/hybris-local/" + name for name in
     (*LICENSES, "downstream.patch", "local.patch", "source.tar.zst", "headers.tar.zst")}
MAX_SIZE = 128 * 1024 * 1024


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def run(command, **kwargs):
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, **kwargs).stdout


def revision(root, headers=False):
    path = root / "tools/hybris" / ("layer-source.env" if headers else "source.env")
    key = "VULKAN_HEADERS_REVISION" if headers else "HYBRIS_REVISION"
    matches = re.findall(r"^" + key + r"=([0-9a-f]{40})$", path.read_text(), re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("invalid pinned local hybris source revision")
    return matches[0]


def base_identity(root):
    base = json.loads((root / "tools/powervr/release.json").read_bytes())["hybris"]
    return {"archive_sha256": base["sha256"], "tree_sha256": base["tree_sha256"]}


def recipe_inputs(root):
    directory = root / "tools/hybris"
    paths = [directory / name for name in ("component.py", "build_layer.py", "source.env",
             "layer-source.env", "powervr-hal-version.patch")]
    paths += sorted((directory / "patches").glob("*.patch"))
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()) for path in paths}


def check_elf(data):
    if (len(data) < 64 or data[:6] != b"\x7fELF\x02\x01"
            or data[16:18] != b"\x03\x00" or data[18:20] != b"\xb7\x00"):
        raise ValueError("local hybris layer must be ELF64 AArch64 shared library")


def checksums(files):
    return "".join(f"{sha256(files[name])}  {name}\n"
                   for name in sorted(files) if name != "SHA256SUMS").encode()


def verify_files(files, manifest, root=ROOT):
    if set(manifest) != {"format", "kind", "base", "source", "headers", "recipe_inputs", "files"}:
        raise ValueError("invalid local hybris manifest fields")
    if manifest["format"] != 1 or manifest["kind"] != "local-compat-layer":
        raise ValueError("unsupported local hybris component")
    if manifest["base"] != base_identity(root):
        raise ValueError("local hybris base bridge pin changed")
    if manifest["recipe_inputs"] != recipe_inputs(root):
        raise ValueError("local hybris build recipe changed; rebuild the component")
    for key, headers in (("source", False), ("headers", True)):
        info = manifest[key]
        if (set(info) != {"revision", "patched_tree"} or info["revision"] != revision(root, headers)
                or not re.fullmatch(r"[0-9a-f]{40}", info["patched_tree"])):
            raise ValueError("local hybris source revision mismatch: " + key)
    if set(files) != set(DESTINATIONS) or set(manifest["files"]) != set(DESTINATIONS):
        raise ValueError("unexpected or missing local hybris component files")
    if sum(map(len, files.values())) > MAX_SIZE:
        raise ValueError("local hybris component exceeds size limit")
    for name, data in files.items():
        if not data or manifest["files"][name] != {"size": len(data), "sha256": sha256(data)}:
            raise ValueError("local hybris component checksum mismatch: " + name)
    for name, key in (("source-commit", "source"), ("headers-source-commit", "headers")):
        if files[name] != (manifest[key]["revision"] + "\n").encode():
            raise ValueError("local hybris source-commit mismatch")
    if files["SHA256SUMS"] != checksums(files):
        raise ValueError("local hybris checksum list mismatch")
    check_elf(files[LAYER])
    build = json.loads(files["build-info.json"])
    if (files["build-info.json"] != json_bytes(build)
            or set(build) != {"format", "scope", "toolchain", "sources", "compile_flags", "link_flags"}
            or build["format"] != 1 or build["scope"] != "compat-layer-only"
            or set(build["toolchain"]) != {"compiler", "target", "linker", "strip", "zstd"}
            or not all(isinstance(v, str) and v.strip() for v in build["toolchain"].values())
            or not re.fullmatch(r"aarch64[^\s]*", build["toolchain"]["target"])
            or not isinstance(build["sources"], dict) or not build["sources"]
            or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
                       for v in build["sources"].values())
            or not build["compile_flags"] or not build["link_flags"]):
        raise ValueError("incomplete local hybris build provenance")


def read_directory(directory, root=ROOT):
    paths = {}
    for path in directory.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("local hybris component contains a symlink or special file")
        if path.is_file():
            paths[path.relative_to(directory).as_posix()] = path
    if set(paths) != set(DESTINATIONS) | {MANIFEST}:
        raise ValueError("unexpected or missing local hybris component files")
    if sum(p.stat().st_size for p in paths.values()) > MAX_SIZE:
        raise ValueError("local hybris component exceeds size limit")
    data = paths.pop(MANIFEST).read_bytes()
    manifest = json.loads(data)
    if data != json_bytes(manifest):
        raise ValueError("local hybris manifest must be canonical JSON")
    files = {name: path.read_bytes() for name, path in paths.items()}
    verify_files(files, manifest, root)
    return files, manifest


def snapshot(repository, pinned, patches=()):
    def git(*args, **kwargs):
        return run(["git", "-C", str(repository), *args], **kwargs)

    if git("rev-parse", "HEAD").decode().strip() != pinned:
        raise ValueError("local hybris source HEAD differs from pinned revision")
    with tempfile.TemporaryDirectory(prefix="hybris-layer-index-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git("read-tree", pinned, env=env)
        for patch in patches:
            git("apply", "--cached", "-", input=patch, env=env)
        tree = git("write-tree", env=env).decode().strip()
        downstream = git("diff", "--cached", "--binary", "--full-index", pinned, env=env)
    entries = {}
    for record in git("ls-tree", "-rz", tree).split(b"\0"):
        if not record:
            continue
        metadata, relative = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = relative.decode()
        if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                or kind != "blob" or mode not in ("100644", "100755", "120000")):
            raise ValueError("unsupported local hybris source entry: " + name)
        path = repository / name
        if mode == "120000":
            if not path.is_symlink():
                raise ValueError("local hybris source symlink changed: " + name)
            content = os.readlink(path).encode()
            target = content.decode()
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if (PurePosixPath(target).is_absolute() or "\\" in target
                    or resolved == ".." or resolved.startswith("../")):
                raise ValueError("unsafe local hybris source symlink: " + name)
        else:
            if path.is_symlink() or not path.is_file():
                raise ValueError("missing local hybris source file: " + name)
            content = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if actual != oid:
            raise ValueError("local hybris source differs from declared patches: " + name)
        entries[name] = (mode, content)
    paths = git("ls-files", "--cached", "--others", "-z").split(b"\0")
    if any(path.decode() not in entries for path in paths if path):
        raise ValueError("unexpected files in local hybris source")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, (mode, content) in sorted(entries.items()):
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if mode == "100755" else 0o644
            if mode == "120000":
                info.type, info.linkname = tarfile.SYMTYPE, content.decode()
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    packed = run(["zstd", "-q", "-10", "-T1", "-c"], input=stream.getvalue())
    return {"revision": pinned, "patched_tree": tree}, packed, downstream, entries


def source_snapshot(source, headers, local_patch, root=ROOT):
    if not local_patch or len(local_patch) > 1024 * 1024:
        raise ValueError("explicit local hybris patch must be nonempty and at most 1 MiB")
    directory = root / "tools/hybris"
    patches = [(directory / "powervr-hal-version.patch").read_bytes()]
    patches += [path.read_bytes() for path in sorted((directory / "patches").glob("*.patch"))]
    source_info, source_archive, downstream, entries = snapshot(source, revision(root), (*patches, local_patch))
    headers_info, headers_archive, _, _ = snapshot(headers, revision(root, True))
    files = {name: entries[name][1] for name in LICENSES} | {
        "source-commit": (source_info["revision"] + "\n").encode(),
        "headers-source-commit": (headers_info["revision"] + "\n").encode(),
        "downstream.patch": downstream, "local.patch": local_patch,
        "source.tar.zst": source_archive, "headers.tar.zst": headers_archive,
    }
    return source_info, headers_info, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify",))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    _, manifest = read_directory(args.directory)
    print("Verified local hybris layer: " + sha256(json_bytes(manifest)))


if __name__ == "__main__":
    main()
