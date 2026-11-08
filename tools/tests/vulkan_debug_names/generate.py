#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def read_module(path):
    data = path.read_bytes()
    if len(data) < 20 or len(data) % 4:
        raise ValueError(f"Invalid SPIR-V length: {path}")
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != 0x07230203:
        raise ValueError(f"Invalid SPIR-V magic: {path}")
    instructions = []
    offset = 5
    while offset < len(words):
        count = words[offset] >> 16
        if not count or offset + count > len(words):
            raise ValueError(f"Invalid instruction at word {offset}")
        instructions.append(words[offset:offset + count])
        offset += count
    return words[:5], instructions


def string_words(value):
    data = value.encode("utf-8") + b"\0"
    data += bytes(-len(data) % 4)
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def semantic_instructions(instructions):
    return [ins for ins in instructions if (ins[0] & 0xFFFF) not in (5, 6)]


def main():
    output = Path(sys.argv[1]).resolve()
    header, instructions = read_module(output / "vertex.spv")
    names = {}
    for ins in instructions:
        if (ins[0] & 0xFFFF) == 5:
            names[ins[1]] = struct.pack(f"<{len(ins) - 2}I", *ins[2:]).split(b"\0", 1)[0].decode()
    if not {"uv_in", "uv_out"}.issubset(names.values()):
        raise ValueError("The compiled vertex module is missing the expected OpName instructions")
    variants = {
        "original": ({}, False),
        "same_name": ({"uv_in": "TEXCOORD", "uv_out": "TEXCOORD"}, False),
        "unique_names": ({"uv_in": "INPUT_UV", "uv_out": "OUTPUT_UV"}, False),
        "no_names": ({}, True),
    }
    modules = {}
    manifest = {}
    for name, (renames, strip) in variants.items():
        changed = []
        for ins in instructions:
            opcode = ins[0] & 0xFFFF
            if strip and opcode in (5, 6):
                continue
            if opcode == 5 and names[ins[1]] in renames:
                text = string_words(renames[names[ins[1]]])
                ins = [((len(text) + 2) << 16) | 5, ins[1], *text]
            changed.append(ins)
        if semantic_instructions(changed) != semantic_instructions(instructions):
            raise ValueError(f"A non-name instruction changed in {name}")
        words = header + [word for ins in changed for word in ins]
        data = struct.pack(f"<{len(words)}I", *words)
        path = output / f"{name}.spv"
        path.write_bytes(data)
        subprocess.run(["spirv-val", "--target-env", "vulkan1.3", str(path)], check=True)
        modules[name] = words
        manifest[name] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "non_name_instructions_identical": True,
        }
    fragment_header, fragment_instructions = read_module(output / "fragment.spv")
    modules["fragment"] = fragment_header + [word for ins in fragment_instructions for word in ins]
    subprocess.run(["spirv-val", "--target-env", "vulkan1.3", str(output / "fragment.spv")], check=True)
    arrays = []
    for name, words in modules.items():
        lines = [", ".join(f"0x{word:08x}" for word in words[i:i + 8]) for i in range(0, len(words), 8)]
        arrays.append(f"static const uint32_t shader_{name}[] = {{\n    " + ",\n    ".join(lines) + "\n};")
    (output / "shaders.h").write_text("\n\n".join(arrays) + "\n")
    (output / "shader-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
