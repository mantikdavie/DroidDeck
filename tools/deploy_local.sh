#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sdk_dir=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-"${HOME}/Library/Android/sdk"}}
adb_bin=${ADB:-${sdk_dir}/platform-tools/adb}
apk=${1:-"${repo_root}/app/build/outputs/apk/release/app-release.apk"}

if [[ ! -x "${adb_bin}" ]]; then
    echo "adb not found at ${adb_bin}; set ADB or ANDROID_HOME." >&2
    exit 1
fi
if [[ ! -f "${apk}" ]]; then
    echo "APK not found at ${apk}; run tools/build_local.sh first." >&2
    exit 1
fi
build_tools=$(find "${sdk_dir}/build-tools" -mindepth 1 -maxdepth 1 -type d -print | sort -V | tail -1)
[[ -x "${build_tools}/aapt" ]] || { echo "Android build-tools with aapt are required" >&2; exit 1; }
package=$("${build_tools}/aapt" dump badging "${apk}" | sed -n "s/^package: name='\([^']*\)'.*/\1/p")
[[ -n "${package}" ]] || { echo "Cannot read the APK package name" >&2; exit 1; }

serial=$(python3 "${repo_root}/tools/adb_device.py" --adb "${adb_bin}")

"${adb_bin}" -s "${serial}" install -r --no-incremental "${apk}"
installed_path=$("${adb_bin}" -s "${serial}" shell pm path "${package}" | tr -d '\r')
printf 'Installed on %s: %s\n' "${serial}" "${installed_path}"
