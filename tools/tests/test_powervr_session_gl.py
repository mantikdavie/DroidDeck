import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BIN = Path(__file__).resolve().parents[1] / "linuxfs/overlay/usr/local/bin"
GAME = runpy.run_path(str(BIN / "droiddeck-game-env"))
FEX = runpy.run_path(str(BIN / "droiddeck-fex"))
GL_KEYS = ("GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE", "LIBGL_ALWAYS_SOFTWARE")
VULKAN = {"VK_DRIVER_FILES": "/system/powervr/icd.json",
          "VK_ICD_FILENAMES": "/system/powervr/icd.json",
          "VK_INSTANCE_LAYERS": "VK_LAYER_HYBRIS_compat",
          "VK_LAYER_PATH": "/system/powervr/lib"}


class PowerVRSessionGlTest(unittest.TestCase):
    def session(self, original=None, mode="steam", enabled="1", inside=None, repeat=False):
        source = (BIN / "droiddeck-session").read_text()
        start = source.index("prepare_powervr_ui_gl() {")
        end = source.index("\nprepare_powervr_ui_gl\n", start)
        script = source[start:end] + "\nprepare_powervr_ui_gl\n"
        if repeat:
            script += "BL_INSIDE=1; prepare_powervr_ui_gl\n"
        script += shlex.quote(sys.executable) + " -c 'import json, os; print(json.dumps(dict(os.environ)))'\n"
        env = {key: value for key, value in os.environ.items()
               if key not in GL_KEYS and not key.startswith("DROIDDECK_GAME_GL_")}
        env.pop("BL_INSIDE", None)
        env.update(VULKAN, mode=mode, BL_POWERVR_SOFTWARE_GL=enabled)
        env.update(original or {})
        if inside is not None:
            env["BL_INSIDE"] = inside
        result = subprocess.run(["bash", "-uc", script], env=env,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def test_selected_steam_ui_uses_software_gl_and_keeps_vulkan_driver(self):
        env = self.session({"GALLIUM_DRIVER": "zink", "MESA_LOADER_DRIVER_OVERRIDE": "zink"})
        self.assertEqual([env[key] for key in GL_KEYS], ["llvmpipe", "swrast", "1"])
        self.assertEqual(env["DROIDDECK_GAME_GL_GALLIUM_DRIVER"], "zink")
        self.assertEqual(env["DROIDDECK_GAME_GL_MESA_LOADER_DRIVER_OVERRIDE"], "zink")
        self.assertNotIn("DROIDDECK_GAME_GL_LIBGL_ALWAYS_SOFTWARE", env)
        self.assertEqual({key: env[key] for key in VULKAN}, VULKAN)

    def test_other_profiles_desktop_and_inner_session_keep_their_gl_environment(self):
        for options in ({"enabled": "0"}, {"mode": "desktop"}, {"inside": "1"}):
            with self.subTest(options=options):
                env = self.session({"GALLIUM_DRIVER": "zink"}, **options)
                self.assertEqual(env["GALLIUM_DRIVER"], "zink")
                self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", env)
                self.assertNotIn("DROIDDECK_GAME_GL_SAVED", env)

    def test_original_values_and_unset_state_survive_inner_session_reentry(self):
        for original in ({}, {"GALLIUM_DRIVER": "", "LIBGL_ALWAYS_SOFTWARE": "0"},
                         {"GALLIUM_DRIVER": "zink", "MESA_LOADER_DRIVER_OVERRIDE": 'literal $(false) " value'}):
            with self.subTest(original=original):
                env = self.session(original, repeat=True)
                before = dict(env)
                restored = GAME["game_gpu"](env, restore_session_gl=True)
                self.assertEqual({key: restored[key] for key in GL_KEYS if key in restored}, original)
                self.assertEqual({key: restored[key] for key in VULKAN}, VULKAN)
                self.assertFalse(any(key.startswith("DROIDDECK_GAME_GL_") for key in restored))
                self.assertEqual(env, before)
                self.assertIs(GAME["game_gpu"](restored, restore_session_gl=True), restored)

    def test_proton_entry_restores_only_actual_game_launches(self):
        class ReachedLaunchBoundary(Exception):
            pass

        inherited = self.session({"GALLIUM_DRIVER": "zink"})
        for verb, app, executable, restore in (("waitforexitandrun", "42", True, True),
                                                ("runinprefix", "42", True, False),
                                                ("run", "42", True, False),
                                                ("waitforexitandrun", "0", True, False),
                                                ("waitforexitandrun", "42", False, False)):
            with self.subTest(verb=verb, app=app, executable=executable):
                command = ["droiddeck-game-env", "proton", verb] + (["game.exe"] if executable else [])
                captured = {}

                def capture(command, env):
                    captured.update(env)
                    raise ReachedLaunchBoundary()

                with mock.patch.dict(os.environ, dict(inherited, STEAM_COMPAT_DATA_PATH="/compat/" + app), clear=True), \
                     mock.patch.object(sys, "argv", command), \
                     mock.patch.dict(GAME["main"].__globals__, {"mark_install_scripts": capture}), \
                     self.assertRaises(ReachedLaunchBoundary):
                    GAME["main"]()
                self.assertEqual(captured["GALLIUM_DRIVER"], "zink" if restore else "llvmpipe")
                self.assertEqual({key: captured[key] for key in VULKAN}, VULKAN)

    def test_native_steam_game_restores_gl_before_per_game_preferences(self):
        inherited = self.session({"GALLIUM_DRIVER": "zink", "MESA_LOADER_DRIVER_OVERRIDE": "zink"})
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "game-environment.json"
            config.write_text(json.dumps({"version": 1, "games": {"43": {"GALLIUM_DRIVER": "user-choice"}}}))
            with mock.patch.dict(FEX["game_environment"].__globals__, {
                "BIN_DIR": str(BIN), "GAME_ENVIRONMENT": str(config),
            }):
                for app, expected in (("42", "zink"), ("43", "user-choice"), ("0", "llvmpipe")):
                    with self.subTest(app=app):
                        result = FEX["game_environment"](dict(inherited, SteamAppId=app), ["/absent/game"])
                        self.assertEqual(result["GALLIUM_DRIVER"], expected)
                        self.assertEqual({key: result[key] for key in VULKAN}, VULKAN)
                        if app != "0":
                            self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", result)
                            self.assertNotIn("DROIDDECK_GAME_GL_SAVED", result)

    def x64_direct(self, inherited, local=None):
        source = (BIN / "droiddeck-steam-x64").read_text()
        start = source.index("x64_env() {")
        writer = source[start:source.index("\nx64_manifest_version() {", start)]
        direct = source.split('cat > "$tool/run" <<\'RUN\'\n', 1)[1].split("\nRUN\n", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "run"
            launcher.write_text(direct)
            (root / "direct-sniper").write_text("42\n")
            if local is not None:
                (root / "game-gpu.local").write_text("export GALLIUM_DRIVER=" + shlex.quote(local) + "\n")
            dump = "import json, os; print(json.dumps(dict(os.environ)))"
            shell = "x64_gpu() { return 0; }\n" + writer + "\nx64_env\n"
            shell += shlex.join([sys.executable, "-c", dump]) + "\n"
            shell += shlex.join(["sh", str(launcher), "waitforexitandrun", sys.executable, "-c", dump]) + "\n"
            env = dict(inherited, X64_HOME=str(root), ROOTFS=str(root), STEAM_X64=str(root),
                       STEAM_COMPAT_APP_ID="42", BL_X64_CLIENT_SOFTWARE="0", BL_X64_GAME_SOFTWARE="0")
            result = subprocess.run(["bash", "-uc", shell], env=env,
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return [json.loads(line) for line in result.stdout.splitlines()]

    def test_x64_direct_games_use_original_gl_values_and_unset_state(self):
        for original in ({}, {"GALLIUM_DRIVER": "zink", "MESA_LOADER_DRIVER_OVERRIDE": "zink"},
                         {"GALLIUM_DRIVER": "", "LIBGL_ALWAYS_SOFTWARE": "0"},
                         {"MESA_LOADER_DRIVER_OVERRIDE": "literal ' $(false) value"}):
            with self.subTest(original=original):
                ui, game = self.x64_direct(self.session(original))
                self.assertEqual(ui["GALLIUM_DRIVER"], "llvmpipe")
                self.assertEqual(ui["MESA_LOADER_DRIVER_OVERRIDE"], "swrast")
                self.assertEqual(ui["DROIDDECK_GAME_GL_SAVED"], "1")
                self.assertEqual({key: game[key] for key in GL_KEYS if key in game}, original)
                self.assertEqual({key: game[key] for key in VULKAN}, VULKAN)
                self.assertFalse(any(key.startswith("DROIDDECK_GAME_GL_") for key in game))

    def test_x64_direct_game_local_preferences_still_override_saved_gl(self):
        ui, game = self.x64_direct(self.session({"GALLIUM_DRIVER": "zink"}), local="user-choice")
        self.assertEqual(ui["GALLIUM_DRIVER"], "llvmpipe")
        self.assertEqual(game["GALLIUM_DRIVER"], "user-choice")
        self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", game)
        self.assertEqual({key: game[key] for key in VULKAN}, VULKAN)

    def test_x64_direct_games_without_ui_workaround_keep_existing_gpu_environment(self):
        ui, game = self.x64_direct(self.session({"GALLIUM_DRIVER": "zink", "MESA_LOADER_DRIVER_OVERRIDE": "zink"},
                                               enabled="0"))
        for env in (ui, game):
            self.assertEqual(env["GALLIUM_DRIVER"], "zink")
            self.assertEqual(env["MESA_LOADER_DRIVER_OVERRIDE"], "zink")
            self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", env)
            self.assertEqual({key: env[key] for key in VULKAN}, VULKAN)


if __name__ == "__main__":
    unittest.main()
