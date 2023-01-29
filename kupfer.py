#!/usr/bin/python3
"""
kupfer      A convenient command and access tool

Copyright 2007--2017 Ulrik Sverdrup

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

try:
    import stackprinter

    stackprinter.set_excepthook(style="color")
except ImportError:
    try:
        from rich.traceback import install

        install()
    except ImportError:
        pass
try:
    import icecream

    icecream.install()
    icecream.ic.configureOutput(includeContext=True)
except ImportError:  # Graceful fallback if IceCream isn't installed.
    pass


try:
    from typeguard.importhook import install_import_hook
 #   from typeguard import config, warn_on_error

    install_import_hook("kupfer")
#    config.typecheck_fail_callback = warn_on_error
except ImportError:
    pass


if __name__ == '__main__':
    from kupfer import main
    main.main()
