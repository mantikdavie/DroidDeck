import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROGRAM = r'''
#include <stdio.h>
#include "compact_va.h"
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    printf("%llu\n", (unsigned long long) dd_proot_pic_address(
        atoi(argv[3]) ? UINT64_C(0x3f00000000) : UINT64_C(0x3000000000),
        (unsigned) atoi(argv[1]), (unsigned) atoi(argv[2]), atoi(argv[3])));
    return 0;
}
'''


@unittest.skipUnless(shutil.which("cc"), "C compiler required")
class ProotCompactVaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        source = Path(cls.work.name) / "compact_va.c"
        source.write_text(PROGRAM)
        cls.binaries = {}
        for arm64 in (True, False):
            binary = Path(cls.work.name) / ("arm64" if arm64 else "other")
            subprocess.run([
                "cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                "-D__aarch64__" if arm64 else "-U__aarch64__",
                "-I", str(TOOLS / "proot"), str(source), "-o", str(binary),
            ], check=True)
            cls.binaries[arm64] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def address(self, setting, elf_class=2, machine=183, interpreter=False, arm64=True):
        env = dict(os.environ)
        env.pop("PROOT_COMPACT_VA", None)
        if setting is not None:
            env["PROOT_COMPACT_VA"] = setting
        return int(subprocess.check_output([
            self.binaries[arm64], str(elf_class), str(machine), str(int(interpreter)),
        ], env=env))

    def test_default_and_non_native_programs_keep_stock_layout(self):
        for interpreter, stock in ((False, 0x3000000000), (True, 0x3f00000000)):
            for setting in (None, "", "0", "true", "01", "1 "):
                with self.subTest(interpreter=interpreter, setting=setting):
                    self.assertEqual(stock, self.address(setting, interpreter=interpreter))
            for elf_class, machine, arm64 in ((1, 40, True), (1, 183, True),
                                              (2, 62, True), (2, 183, False)):
                with self.subTest(elf_class=elf_class, machine=machine, arm64=arm64):
                    self.assertEqual(stock, self.address("1", elf_class, machine, interpreter, arm64))

    def test_opt_in_leaves_room_for_a_256_gib_reservation(self):
        executable = self.address("1")
        interpreter = self.address("1", interpreter=True)
        loader_end = 0x2000000000 + 0x1000
        wine_modules_start = 0x6F00000000
        self.assertGreaterEqual(executable - loader_end, 256 << 30)
        self.assertGreaterEqual(interpreter - executable, 1 << 30)
        self.assertLess(interpreter, wine_modules_start)
        self.assertLess(wine_modules_start, 1 << 39)


if __name__ == "__main__":
    unittest.main()
