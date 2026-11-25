"""Main program entrypoint."""

import argparse
import atexit
import os
import pathlib
import platform
import signal
import sys
from dataclasses import dataclass

from modules.runtime import get_base_path
from modules.version import pokebot_name, pokebot_version

OS_NAME = platform.system()
gui = None


#Extracted helper to clarify Windows launcher check
def is_launched_from_py_launcher() -> bool:
    """Return True if the process was started directly via Windows py.exe."""
    import psutil

    parent_process_name = psutil.Process(os.getppid()).name()
    return parent_process_name == "py.exe"


# On Windows, the bot can be started by clicking this Python file. In that case, the terminal
# window is only open for as long as the bot runs, which would make it impossible to see error
# messages during a crash.
# For those cases, we register an `atexit` handler that will wait for user input before closing
# the terminal window.
def on_exit() -> None:
    if OS_NAME != "Windows" or not is_launched_from_py_launcher():
        return

    if gui is not None and gui.window is not None:
        gui.window.withdraw()

    input("\nPress Enter to close...")


atexit.register(on_exit)


@dataclass
class StartupSettings:
    profile: "Profile | None"
    debug: bool
    bot_mode: str
    headless: bool
    no_video: bool
    no_audio: bool
    no_theme: bool
    use_opengl: bool
    emulation_speed: int
    always_on_top: bool
    config_path: str

    #Method to create initialize startup settings from arguments
    @classmethod
    def from_args(cls, args: argparse.Namespace, preselected_profile: "Profile | None") -> "StartupSettings":
        return cls(
            profile=preselected_profile,
            debug=bool(args.debug),
            bot_mode=args.bot_mode or "Manual",
            headless=bool(args.headless),
            no_video=bool(args.no_video),
            no_audio=bool(args.no_audio),
            no_theme=bool(args.no_theme),
            use_opengl=bool(args.use_opengl),
            emulation_speed=int(args.emulation_speed or "1"),
            always_on_top=bool(args.always_on_top),
            config_path=args.config_path,
        )


#Checks if it is a valid directory
def is_valid_directory(path_obj: pathlib.Path) -> bool:
    return path_obj.exists() and path_obj.is_dir()


def directory_arg(value: str) -> pathlib.Path:
    """Determine if the value is a valid readable directory.

    :param value: Directory to verify.
    """
    path_obj = pathlib.Path(value)
    if not is_valid_directory(path_obj):
        from modules import exceptions

        raise exceptions.CriticalDirectoryMissing(value)
    return path_obj


def parse_arguments(bot_mode_names: list[str]) -> StartupSettings:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description=f"{pokebot_name} {pokebot_version}")
    parser.add_argument(
        "profile",
        nargs="?",
        help="Profile to initialize. Otherwise, the profile selection menu will appear.",
    )
    parser.add_argument("-m", "--bot-mode", choices=bot_mode_names, help="Initial bot mode (default: Manual).")
    parser.add_argument(
        "-s",
        "--emulation-speed",
        choices=["0", "1", "2", "3", "4", "8", "16", "32"],
        help="Initial emulation speed (0 for unthrottled; default: 1)",
    )
    parser.add_argument("-hl", "--headless", action="store_true", help="Run without a GUI, only using the console.")
    parser.add_argument("-nv", "--no-video", action="store_true", help="Turn off video output by default.")
    parser.add_argument("-na", "--no-audio", action="store_true", help="Turn off audio output by default.")
    parser.add_argument("-nt", "--no-theme", action="store_true", help="Turn off the fancy GUI theme.")
    parser.add_argument(
        "-gl", "--use-opengl", action="store_true", help="Use OpenGL to render the video output (potentially faster)"
    )
    parser.add_argument(
        "-t", "--always-on-top", action="store_true", help="Keep the bot window always on top of other windows."
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Enable extra debug options and a debug menu.")
    parser.add_argument("-c", "--config", type=directory_arg, dest="config_path", help=argparse.SUPPRESS)
    args = parser.parse_args()

    preselected_profile: Profile | None = None
    if args.profile and profile_directory_exists(args.profile):
        preselected_profile = load_profile_by_name(args.profile)

    return StartupSettings.from_args(args, preselected_profile)


#Rename / Replace Magic Number
def register_signal_handlers(context) -> None:
    """Register OS-specific shutdown handlers to safely save emulator state."""
    if OS_NAME == "Windows":
        import win32api
        import win32con

        def win32_signal_handler(control_event: int) -> None:
            if control_event == win32con.CTRL_CLOSE_EVENT and context.emulator is not None:
                context.emulator.shutdown()

        win32api.SetConsoleCtrlHandler(win32_signal_handler, True)
    #Create a termination handler if something goes wrong"
    def sigterm_handler(signal_number, stack_frame) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if context.emulator is not None:
            context.emulator.shutdown()
            context.emulator = None
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    signal.signal(signal.SIGTERM, sigterm_handler)


if __name__ == "__main__":

    can_update_and_install_dependencies = not (get_base_path() / ".is_frozen").exists()

    if can_update_and_install_dependencies:
        from requirements import check_requirements

        check_requirements()

    from modules.debug import debug
    from modules.context import context
    from modules.console import console
    from modules.exceptions_hook import register_exception_hook
    from modules.game import set_rom
    from modules.libmgba import LibmgbaEmulator
    from modules.main import main_loop
    from modules.modes import get_bot_mode_names
    from modules.plugins import load_plugins
    from modules.profiles import Profile, profile_directory_exists, load_profile_by_name

    register_exception_hook()
    load_plugins()
    register_signal_handlers(context)

    startup_settings = parse_arguments(get_bot_mode_names())
    console.print(f"Starting [bold cyan]{pokebot_name} {pokebot_version}![/]")

    #Better wording to explain when auto-updater should run
    is_not_git_install = can_update_and_install_dependencies and not (get_base_path() / ".git").is_dir()
    if is_not_git_install:
        from updater import run_updater

        run_updater()

    if startup_settings.headless:
        from modules.gui.headless import PokebotHeadless

        gui = PokebotHeadless(on_exit)
    else:
        from modules.gui import PokebotGui

        no_theme = os.getenv("POKEBOT_UNTHEMED") == "1" or startup_settings.no_theme
        gui = PokebotGui(
            on_exit,
            no_theme=no_theme,
            always_on_top=startup_settings.always_on_top,
            use_opengl=startup_settings.use_opengl,
        )

    profile = startup_settings.profile if startup_settings.profile is not None else gui.run_profile_selection()
    if profile is None:
        raise RuntimeError("No profile was selected.")

    #Clarifying variable assignment
    is_debug_enabled = startup_settings.debug and not startup_settings.headless

    context.profile = profile
    context.config.load(profile.path, strict=False)
    set_rom(profile.rom)
    context.emulator = LibmgbaEmulator(profile, gui.on_frame)
    context.audio = not startup_settings.no_audio
    context.video = not startup_settings.no_video
    context.emulation_speed = startup_settings.emulation_speed
    context.debug = is_debug_enabled
    context.bot_mode = startup_settings.bot_mode
    context.gui = gui

    if context.debug:
        debug.enabled = True

    gui.run_profile()
    main_loop()
