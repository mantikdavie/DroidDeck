#!/usr/bin/env python3
"""Build a source-verified local hybris compatibility layer, not the whole bridge."""
import argparse
import concurrent.futures
import importlib.util
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hybris_component", ROOT / "tools/hybris/component.py")
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)
COMPILE_FLAGS = ["-O2", "-Wall", "-Wextra", "-fPIC", "-fvisibility=hidden", "-pthread"]
LINK_FLAGS = ["-shared", "-pthread", "-Wl,-z,nodelete", "-Wl,--no-undefined",
              "-Wl,-soname,libVkLayer_hybris_compat.so"]


def build(source, headers, patch, output, jobs=2, root=ROOT):
    if output.exists() and any(output.iterdir()):
        raise ValueError("local hybris output must be empty")
    local_patch = patch.read_bytes()
    source_info, headers_info, files = component.source_snapshot(source, headers, local_patch, root)
    commands = {"compiler": ["gcc", "--version"], "target": ["gcc", "-dumpmachine"],
                "linker": ["ld", "--version"], "strip": ["strip", "--version"],
                "zstd": ["zstd", "--version"]}
    toolchain = {name: component.run(command).decode().strip() for name, command in commands.items()}
    if not re.fullmatch(r"aarch64[^\s]*", toolchain["target"]):
        raise ValueError("local hybris layer requires a native AArch64 GCC toolchain")
    vulkan = source / "hybris/vulkan"
    recipe = (vulkan / "Makefile.am").read_text()
    section = recipe.split("libVkLayer_hybris_compat_la_SOURCES = ", 1)[1].split(
        "libVkLayer_hybris_compat_la_CFLAGS", 1)[0]
    sources = re.findall(r"[\w./-]+\.c\b", section)
    if not sources or len(sources) != len(set(sources)):
        raise ValueError("invalid hybris compatibility-layer source list")
    source_hashes = {name: component.sha256((vulkan / name).read_bytes()) for name in sources}
    with tempfile.TemporaryDirectory(prefix="hybris-layer-compile-") as directory:
        temporary = Path(directory)

        def compile_file(name):
            obj = temporary / (name.replace("/", "_") + ".o")
            component.run(["gcc", *COMPILE_FLAGS, "-I" + str(headers / "include"), "-c",
                           str(vulkan / name), "-o", str(obj)])
            return obj

        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as workers:
            objects = list(workers.map(compile_file, sources))
        layer = temporary / component.LAYER
        component.run(["gcc", *LINK_FLAGS, *map(str, objects), "-o", str(layer)])
        component.run(["strip", "--strip-unneeded", str(layer)])
        files[component.LAYER] = layer.read_bytes()
    after_source, after_headers, after_files = component.source_snapshot(source, headers, local_patch, root)
    if (after_source != source_info or after_headers != headers_info
            or any(files[name] != data for name, data in after_files.items())):
        raise ValueError("local hybris source changed during compilation")
    files["build-info.json"] = component.json_bytes({"format": 1, "scope": "compat-layer-only",
        "toolchain": toolchain, "sources": source_hashes,
        "compile_flags": COMPILE_FLAGS, "link_flags": LINK_FLAGS})
    files["SHA256SUMS"] = component.checksums(files)
    manifest = {"format": 1, "kind": "local-compat-layer", "base": component.base_identity(root),
        "source": source_info, "headers": headers_info, "recipe_inputs": component.recipe_inputs(root),
        "files": {name: {"size": len(data), "sha256": component.sha256(data)} for name, data in files.items()}}
    component.verify_files(files, manifest, root)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / component.MANIFEST).write_bytes(component.json_bytes(manifest))
    component.read_directory(output, root)
    print("Local hybris compatibility layer built: " + component.sha256(component.json_bytes(manifest)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, required=True, help="pinned checkout with declared patches already applied")
    parser.add_argument("--headers", type=Path, required=True, help="clean pinned Vulkan-Headers checkout")
    parser.add_argument("--extra-patch", type=Path, required=True, help="explicit local patch after the repository patches")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs must be between 1 and 64")
    build(args.source.resolve(), args.headers.resolve(), args.extra_patch.resolve(), args.output.resolve(), args.jobs)


if __name__ == "__main__":
    main()
