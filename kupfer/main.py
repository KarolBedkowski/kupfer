import typing as ty
import gettext
import locale
import sys
from contextlib import suppress
import getopt

_DEBUG = False

if ty.TYPE_CHECKING:
    _ = str


def _setup_locale_and_gettext() -> None:
    """Set up localization with gettext"""
    package_name = "kupfer"
    localedir = "./locale"
    try:
        from kupfer import version_subst
    except ImportError:
        pass
    else:
        package_name = version_subst.PACKAGE_NAME
        localedir = version_subst.LOCALEDIR
    # Install _() builtin for gettext; always returning unicode objects
    # also install ngettext()
    gettext.install(
        package_name, localedir=localedir, names=("ngettext",)  # unicode=True,
    )
    # For Gtk.Builder, we need to call the C library gettext functions
    # As well as set the codeset to avoid locale-dependent translation
    # of the message catalog
    locale.bindtextdomain(package_name, localedir)
    locale.bind_textdomain_codeset(package_name, "UTF-8")
    # to load in current locale properly for sorting etc
    with suppress(locale.Error):
        locale.setlocale(locale.LC_ALL, "")


_setup_locale_and_gettext()


def prt(*args: ty.Any) -> None:
    enc = locale.getpreferredencoding(do_setlocale=False)
    sys.stdout.buffer.write(" ".join(args).encode(enc, "replace"))
    sys.stdout.buffer.write(b"\n")
    # print(((" ".join(args)).encode(enc, "replace")))


def _make_help_text(
    program_options: list[tuple[str, str]], misc_options: list[tuple[str, str]]
) -> str:
    usage_string = _("Usage: kupfer [ OPTIONS | FILE ... ]")

    def format_options(opts):
        return "\n".join(f"  --{o:<15}  {h}" for o, h in opts)

    popts = format_options(program_options)
    mopts = format_options(misc_options)
    options_string = f"{usage_string}\n\n{popts}\n\n{mopts}\n"
    return options_string


def _make_plugin_list():
    from kupfer.core import plugins

    plugin_header = _("Available plugins:")
    plugin_list = plugins.get_plugin_desc()
    return "\n".join((plugin_header, plugin_list))


def get_options() -> list[str]:
    """Return a list of other application flags with --* prefix included."""

    program_options = [
        ("no-splash", _("do not present main interface on launch")),
        ("list-plugins", _("list available plugins")),
        ("debug", _("enable debug info")),
        # TRANS: --exec-helper=HELPER is an internal command
        # TRANS: that executes a helper program that is part of kupfer
        ("exec-helper=", _("run plugin helper")),
    ]
    misc_options = [
        ("help", _("show usage help")),
        ("version", _("show version information")),
    ]

    # Fix sys.argv that can be None in exceptional cases
    if sys.argv[0] is None:
        sys.argv[0] = "kupfer"

    try:
        opts, _args = getopt.getopt(
            sys.argv[1:],
            "",
            [o for o, _h in program_options] + [o for o, _h in misc_options],
        )
    except getopt.GetoptError as exc:
        prt(str(exc))
        prt(_make_help_text(program_options, misc_options))
        raise SystemExit(1)

    for key, val in opts:
        if key == "--list-plugins":
            prt(gtkmain(_make_plugin_list))
            raise SystemExit

        if key == "--help":
            prt(_make_help_text(program_options, misc_options))
            raise SystemExit

        if key == "--version":
            print_version()
            raise SystemExit

        if key == "--debug":
            global _DEBUG
            _DEBUG = True

        if key == "--relay":
            prt("WARNING: --relay is deprecated!")
            exec_helper("kupfer.keyrelay")
            raise SystemExit

        if key == "--exec-helper":
            exec_helper(val)
            raise SystemExit(1)

    # return list first of tuple pair
    return [tupl[0] for tupl in opts]


def print_version() -> None:
    from kupfer import version

    prt(version.PACKAGE_NAME, version.VERSION)


def print_banner() -> None:
    from kupfer import version

    if not sys.stdout or not sys.stdout.isatty():
        return

    banner = _(
        "%(PROGRAM_NAME)s: %(SHORT_DESCRIPTION)s\n"
        "   %(COPYRIGHT)s\n"
        "   %(WEBSITE)s\n"
    ) % vars(version)
    prt(banner)


def _set_process_title() -> None:
    try:
        import setproctitle
    except ImportError:
        pass
    else:
        setproctitle.setproctitle("kupfer")


def exec_helper(helpername: str) -> None:
    import runpy

    runpy.run_module(helpername, run_name="__main__", alter_sys=True)
    raise SystemExit


def gtkmain(
    run_function: ty.Callable[[ty.Any], ty.Any],
    *args: ty.Any,
    **kwargs: ty.Any,
) -> ty.Any:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")

    return run_function(*args, **kwargs)


def browser_start(quiet: bool) -> None:
    from gi.repository import Gdk

    if not Gdk.Screen.get_default():
        print("No Screen Found, Exiting...", file=sys.stderr)
        sys.exit(1)

    from kupfer.ui import browser

    wctrl = browser.WindowController()
    wctrl.main(quiet=quiet)


def main():
    # parse commandline before importing UI
    cli_opts = get_options()
    print_banner()

    from kupfer import pretty, version

    if _DEBUG:
        pretty.debug = _DEBUG
        pretty.print_debug(
            __name__, "Version:", version.PACKAGE_NAME, version.VERSION
        )
        with suppress(ImportError):
            import debug

            debug.install()

    sys.excepthook = sys.__excepthook__
    _set_process_title()

    quiet = "--no-splash" in cli_opts
    gtkmain(browser_start, quiet)
