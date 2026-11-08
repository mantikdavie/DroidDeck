#!/usr/bin/env python3
"""Record and verify an explicitly selected local DXVK build, without release pins."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "component-manifest.json"
DLLS = tuple(f"{arch}/{name}.dll" for arch in ("x32", "x64")
             for name in ("d3d8", "d3d9", "d3d10core", "d3d11", "dxgi"))
DESTINATIONS = {name: "dxvk/" + name for name in DLLS} | {
    "LICENSE": "licenses/dxvk-LICENSE",
    "source-commit": "provenance/dxvk-source-commit",
    "submodules.txt": "provenance/dxvk-submodules.txt",
    "downstream.patch": "licenses/dxvk-downstream.patch",
    "source.tar.zst": "licenses/dxvk-source.tar.zst",
    "SHA256SUMS": "provenance/dxvk-SHA256SUMS",
}
MANIFEST_DESTINATION = "provenance/dxvk-component-manifest.json"
MAX_SIZE = 256 * 1024 * 1024
TOOL_COMMANDS = {
    "cc32": ["i686-w64-mingw32-gcc", "--version"],
    "cxx32": ["i686-w64-mingw32-g++", "--version"],
    "cc64": ["x86_64-w64-mingw32-gcc", "--version"],
    "cxx64": ["x86_64-w64-mingw32-g++", "--version"],
    "meson": ["meson", "--version"], "ninja": ["ninja", "--version"],
    "glslang": ["glslangValidator", "--version"],
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def recipe_inputs(root):
    directory = root / "tools/dxvk"
    paths = [directory / name for name in ("build.sh", "component.py", "source.env")]
    paths += sorted(directory.glob("check_*.py")) + sorted((directory / "patches").glob("*.patch"))
    paths += sorted(path for path in (directory / "tests").rglob("*") if path.is_file())
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()) for path in paths}


def revision(root):
    match = re.fullmatch(r"DXVK_REVISION=([0-9a-f]{40})\s*", (root / "tools/dxvk/source.env").read_text())
    if not match:
        raise ValueError("invalid pinned DXVK revision")
    return match[1]


def check_pe(data, name):
    pe = int.from_bytes(data[60:64], "little")
    machine, magic = (b"\x4c\x01", b"\x0b\x01") if name.startswith("x32/") else (b"\x64\x86", b"\x0b\x02")
    if (data[:2] != b"MZ" or pe < 64 or data[pe:pe + 4] != b"PE\0\0"
            or data[pe + 4:pe + 6] != machine or data[pe + 24:pe + 26] != magic):
        raise ValueError("DXVK library has wrong PE architecture: " + name)


def checksums(files):
    return "".join(f"{sha256(files[name])}  {name}\n" for name in sorted(DLLS)).encode()


def verify_files(files, manifest, root=ROOT):
    if set(manifest) != {"format", "kind", "source", "recipe_inputs", "toolchain", "files"}:
        raise ValueError("invalid local DXVK manifest fields")
    if manifest["format"] != 1 or manifest["kind"] != "local-build":
        raise ValueError("unsupported local DXVK manifest")
    if manifest["recipe_inputs"] != recipe_inputs(root):
        raise ValueError("local DXVK component recipe changed; rebuild the component")
    source = manifest["source"]
    if (set(source) != {"revision", "patched_tree", "submodules"}
            or source["revision"] != revision(root)
            or not re.fullmatch(r"[0-9a-f]{40}", source["patched_tree"])):
        raise ValueError("local DXVK source revision mismatch")
    if (set(manifest["toolchain"]) != set(TOOL_COMMANDS)
            or not all(isinstance(value, str) and value.strip() for value in manifest["toolchain"].values())):
        raise ValueError("incomplete local DXVK toolchain provenance")
    if set(files) != set(DESTINATIONS) or set(manifest["files"]) != set(DESTINATIONS):
        raise ValueError("local DXVK component must contain exactly ten DLLs and source/license provenance")
    if sum(map(len, files.values())) > MAX_SIZE:
        raise ValueError("local DXVK component exceeds size limit")
    for name, content in files.items():
        if not content or manifest["files"][name] != {"size": len(content), "sha256": sha256(content)}:
            raise ValueError("local DXVK component checksum mismatch: " + name)
    if files["source-commit"] != (source["revision"] + "\n").encode():
        raise ValueError("local DXVK source-commit mismatch")
    if files["submodules.txt"] != source["submodules"].encode():
        raise ValueError("local DXVK submodule provenance mismatch")
    for line in source["submodules"].splitlines():
        if not re.fullmatch(r" [0-9a-f]{40} [^\s]+(?: \([^\n]*\))?", line):
            raise ValueError("local DXVK submodule is missing or modified")
    if files["SHA256SUMS"] != checksums(files):
        raise ValueError("local DXVK DLL checksum list mismatch")
    for name in DLLS:
        check_pe(files[name], name)


def read_directory(directory, root=ROOT):
    paths = {}
    for path in directory.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("local DXVK component contains a symlink or special file")
        if path.is_file():
            paths[path.relative_to(directory).as_posix()] = path
    if set(paths) != set(DESTINATIONS) | {MANIFEST}:
        raise ValueError("unexpected or missing local DXVK component files")
    if sum(path.stat().st_size for path in paths.values()) > MAX_SIZE:
        raise ValueError("local DXVK component exceeds size limit")
    manifest_data = paths.pop(MANIFEST).read_bytes()
    manifest = json.loads(manifest_data)
    if manifest_data != json_bytes(manifest):
        raise ValueError("local DXVK manifest must be canonical JSON")
    files = {name: path.read_bytes() for name, path in paths.items()}
    verify_files(files, manifest, root)
    return files, manifest


def run(command, **kwargs):
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **kwargs).stdout


def source_snapshot(source, root):
    def git(*args, **kwargs):
        return run(["git", "-C", str(source), *args], **kwargs)

    pinned = revision(root)
    if git("rev-parse", "HEAD").decode().strip() != pinned:
        raise ValueError("local DXVK source HEAD differs from the pinned revision")
    with tempfile.TemporaryDirectory(prefix="dxvk-index-") as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git("read-tree", pinned, env=env)
        for patch in sorted((root / "tools/dxvk/patches").glob("*.patch")):
            git("apply", "--cached", str(patch), env=env)
        tree = git("write-tree", env=env).decode().strip()
        downstream = git("diff", "--cached", "--binary", "--full-index", pinned, env=env)

    entries, submodules = {}, {}

    def collect(repository, treeish, prefix=""):
        listing = run(["git", "-C", str(repository), "ls-tree", "-rz", treeish])
        expected_paths = set()
        for record in listing.split(b"\0"):
            if not record:
                continue
            metadata, relative = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            name = relative.decode()
            expected_paths.add(name)
            path = repository / name
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("unsafe DXVK source path")
            if kind == "commit":
                head = run(["git", "-C", str(path), "rev-parse", "HEAD"]).decode().strip()
                if head != object_id:
                    raise ValueError("local DXVK submodule revision mismatch: " + prefix + name)
                submodules[prefix + name] = object_id
                collect(path, object_id, prefix + name + "/")
                continue
            if kind != "blob" or mode not in ("100644", "100755", "120000"):
                raise ValueError("unsupported DXVK source file: " + prefix + name)
            if mode == "120000":
                if not path.is_symlink():
                    raise ValueError("DXVK source symlink changed: " + prefix + name)
                content = os.readlink(path).encode()
                target = PurePosixPath(content.decode())
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError("unsafe DXVK source symlink: " + prefix + name)
            else:
                if path.is_symlink() or not path.is_file():
                    raise ValueError("DXVK source file missing: " + prefix + name)
                content = path.read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if digest != object_id:
                raise ValueError("local DXVK source differs from current patches: " + prefix + name)
            entries[prefix + name] = (mode, content)
        actual_paths = run(["git", "-C", str(repository), "ls-files", "--cached", "--others", "-z"])
        if any(name.decode() not in expected_paths for name in actual_paths.split(b"\0") if name):
            raise ValueError("unexpected files in local DXVK source: " + prefix)

    collect(source, tree)
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
    compressed = run(["zstd", "-q", "-10", "-T1", "-c"], input=stream.getvalue())
    submodules = "".join(f" {commit} {path}\n" for path, commit in sorted(submodules.items()))
    return {"revision": pinned, "patched_tree": tree, "submodules": submodules}, {
        "LICENSE": entries["LICENSE"][1], "source-commit": (pinned + "\n").encode(),
        "submodules.txt": submodules.encode(), "downstream.patch": downstream, "source.tar.zst": compressed,
    }


def record(directory, source, root=ROOT):
    for path in directory.rglob("*"):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("local DXVK component contains a symlink or special file")
        if path.is_file() and name not in set(DESTINATIONS) | {MANIFEST}:
            raise ValueError("unexpected local DXVK component file: " + name)
    files = {name: (directory / name).read_bytes() for name in DLLS}
    for name in DLLS:
        check_pe(files[name], name)
    source_info, provenance = source_snapshot(source, root)
    files.update(provenance)
    files["SHA256SUMS"] = checksums(files)
    toolchain = {name: run(command).decode().strip() for name, command in TOOL_COMMANDS.items()}
    manifest = {"format": 1, "kind": "local-build", "source": source_info,
                "recipe_inputs": recipe_inputs(root), "toolchain": toolchain,
                "files": {name: {"size": len(data), "sha256": sha256(data)} for name, data in files.items()}}
    verify_files(files, manifest, root)
    for name, data in provenance.items():
        (directory / name).write_bytes(data)
    (directory / "SHA256SUMS").write_bytes(files["SHA256SUMS"])
    (directory / MANIFEST).write_bytes(json_bytes(manifest))
    read_directory(directory, root)
    print("Local DXVK component recorded: " + sha256(json_bytes(manifest)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.command == "record":
        if args.source is None:
            parser.error("record requires --source pointing to the patched Git checkout")
        record(args.directory.resolve(), args.source.resolve())
    else:
        read_directory(args.directory.resolve())
        print("Local DXVK component verified")


if __name__ == "__main__":
    main()
