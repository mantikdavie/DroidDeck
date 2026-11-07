from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / 'tools/gamescope/patches/0936-screenshot-host-cached-memory-fallback.patch'


def patched_function():
    lines = PATCH.read_text().splitlines()
    source = '\n'.join(line[1:] for line in lines
                       if line.startswith((' ', '+')) and not line.startswith('+++'))
    start = source.index('int32_t CVulkanDevice::findMemoryType(')
    return source[start:source.index('\n}\n', start) + 3]


PREAMBLE = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
using VkMemoryPropertyFlags = uint32_t;
constexpr uint32_t VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT = 1;
constexpr uint32_t VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT = 2;
constexpr uint32_t VK_MEMORY_PROPERTY_HOST_CACHED_BIT = 8;
struct Log {
    template<typename... Args> void infof(const char *, Args...) {}
    template<typename... Args> void errorf(const char *, Args...) {}
} vk_log;
struct CVulkanDevice {
    struct MemoryType { uint32_t propertyFlags = 0; uint32_t heapIndex = 0; };
    struct Properties { uint32_t memoryTypeCount = 0; MemoryType memoryTypes[32]; } m_memoryProperties;
    int32_t findMemoryType(VkMemoryPropertyFlags, uint32_t);
};
'''

MAIN = r'''
int main(int argc, char **argv) {
    CVulkanDevice device;
    device.m_memoryProperties.memoryTypeCount = argc - 3;
    for (int i = 3; i < argc; ++i)
        device.m_memoryProperties.memoryTypes[i - 3].propertyFlags = std::strtoul(argv[i], nullptr, 0);
    std::printf("%d\n", device.findMemoryType(std::strtoul(argv[1], nullptr, 0), std::strtoul(argv[2], nullptr, 0)));
}
'''


@unittest.skipUnless(shutil.which('c++'), 'needs a C++ compiler')
class GamescopeMemoryTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        work = Path(temporary.name)
        source = work / 'memory.cpp'
        cls.binary = work / 'memory'
        source.write_text(PREAMBLE + patched_function() + MAIN)
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(cls.binary)], check=True)

    def select(self, properties, allowed_types, *memory_flags):
        result = subprocess.run([str(self.binary), str(properties), str(allowed_types),
                                 *(str(flags) for flags in memory_flags)], check=True, capture_output=True, text=True)
        return int(result.stdout)

    def test_pixel_screenshot_uses_coherent_uncached_host_memory(self):
        self.assertEqual(1, self.select(0xe, 0x7, 0x1, 0x7, 0x11))

    def test_cached_host_memory_remains_preferred_when_available(self):
        self.assertEqual(3, self.select(0xe, 0xf, 0x1, 0x7, 0x11, 0xe))

    def test_fallback_respects_image_memory_type_mask(self):
        self.assertEqual(-1, self.select(0xe, 0x5, 0x1, 0x7, 0x11))

    def test_screenshot_never_loses_host_coherency(self):
        self.assertEqual(-1, self.select(0xe, 0x7, 0x1, 0x3, 0x11))

    def test_fallback_retains_other_required_properties(self):
        self.assertEqual(-1, self.select(0x2e, 0x7, 0x1, 0x7, 0x11))
        self.assertEqual(-1, self.select(0x8, 0x1, 0x1))

    def test_existing_device_local_host_visible_fallback_remains_available(self):
        self.assertEqual(0, self.select(0x7, 0x1, 0x6))


if __name__ == '__main__':
    unittest.main()
