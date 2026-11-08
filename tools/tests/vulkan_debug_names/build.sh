#!/bin/sh
set -eu
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output_dir=${1:-build/vulkan-debug-names}
mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)

glslangValidator -V --target-env vulkan1.3 "$source_dir/vertex.vert" -o "$output_dir/vertex.spv"
glslangValidator -V --target-env vulkan1.3 "$source_dir/fragment.frag" -o "$output_dir/fragment.spv"
python3 "$source_dir/generate.py" "$output_dir"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -I"$output_dir" \
    "$source_dir/probe.c" -ldl -lm -o "$output_dir/vulkan-debug-names"
printf 'Built %s\n' "$output_dir/vulkan-debug-names"
