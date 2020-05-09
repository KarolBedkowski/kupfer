"""Firefox common functions."""

import os
from configparser import RawConfigParser

from kupfer import pretty


def get_firefox_home_file(needed_file):
    firefox_dir = os.path.expanduser("~/.mozilla/firefox")
    if not os.path.exists(firefox_dir):
        return None

    def make_absolute_and_check(path):
        """Helper, make path absolute and check is exist."""
        if not path.startswith("/"):
            path = os.path.join(firefox_dir, path)

        if os.path.isdir(path):
            return path

        return None


    config = RawConfigParser({"Default" : 0})
    config.read(os.path.join(firefox_dir, "profiles.ini"))
    path = None

    # find Instal.* section and default profile
    for section in config.sections():
        if section.startswith("Install"):
            if not config.has_option(section, "Default"):
                continue

            # found default profile
            path = make_absolute_and_check(config.get(section, "Default"))
            if path:
                pretty.print_debug(__name__, "found install default profile",
                                   path)
                return os.path.join(path, needed_file)

            break

    pretty.print_debug("Install* default profile not found")

    # not found default profile, iterate profiles, try to find default
    for section in config.sections():
        if not section.startswith("Profile"):
            continue

        if config.has_option(section, "Default") and \
                config.get(section, "Default") == "1":
            path = make_absolute_and_check(config.get(section, "Path"))
            if path:
                pretty.print_debug(__name__, "Found profile with default=1",
                                   section, path)
                break

        if not path and config.has_option(section, "Path"):
            path = make_absolute_and_check(config.get(section, "Path"))

    pretty.print_debug(__name__, "Profile path", path)
    return os.path.join(path, needed_file) if path else ""
