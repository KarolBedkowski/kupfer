from __future__ import annotations

import typing as ty
import sys
import traceback
from time import time as timestamp
import types

DEBUG = False

# TODO: move somewhere
ExecInfo = ty.Union[
    tuple[
        ty.Type[BaseException], BaseException, ty.Optional[types.TracebackType]
    ],
    tuple[None, None, None],
]


class OutputMixin:
    """
    A mixin class providing prefixed output
    standard output and DEBUG output
    """

    def _output_category(self) -> str:
        return f"[{type(self).__module__}] {type(self).__name__}:"

    def _output_core(
        self,
        prefix: str,
        sep: str,
        end: str,
        stream: ty.TextIO | None,
        *items: ty.Any,
    ) -> None:
        category = self._output_category()
        print(prefix + category, *items, sep=sep, end=end, file=stream)

    def output_info(self, *items: ty.Any, **kwargs: ty.Any) -> None:
        """
        Output given items using @sep as separator,
        ending the line with @end
        """
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        self._output_core("", sep, end, sys.stdout, *items)

    def output_exc(self, exc_info: ExecInfo | None = None) -> None:
        """Output current exception, or use @exc_info if given"""
        etype, value, tb = exc_info or sys.exc_info()
        assert etype
        if DEBUG:
            self._output_core("Exception in ", "", "\n", sys.stderr)
            traceback.print_exception(etype, value, tb, file=sys.stderr)
            return

        msg = f"{etype.__name__}: {value}"
        self._output_core("Exception in ", " ", "\n", sys.stderr, msg)

    def output_debug(self, *items: ty.Any, **kwargs: ty.Any) -> None:
        if DEBUG:
            sep = kwargs.get("sep", " ")
            end = kwargs.get("end", "\n")
            self._output_core("D ", sep, end, sys.stderr, *items)

    def output_error(self, *items: ty.Any, **kwargs: ty.Any) -> None:
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        self._output_core("Error ", sep, end, sys.stderr, *items)


class _StaticOutput(OutputMixin):
    current_calling_module: str | None = None

    def _output_category(self) -> str:
        return f"[{self.current_calling_module}]:"

    def print_info(
        self, modulename: str, *args: ty.Any, **kwargs: ty.Any
    ) -> None:
        self.current_calling_module = modulename
        self.output_info(*args, **kwargs)

    def print_error(
        self, modulename: str, *args: ty.Any, **kwargs: ty.Any
    ) -> None:
        self.current_calling_module = modulename
        self.output_error(*args, **kwargs)

    def print_exc(
        self, modulename: str, *args: ty.Any, **kwargs: ty.Any
    ) -> None:
        self.current_calling_module = modulename
        self.output_exc(*args, **kwargs)

    def print_debug(
        self, modulename: str, *args: ty.Any, **kwargs: ty.Any
    ) -> None:
        if DEBUG:
            self.current_calling_module = modulename
            self.output_debug(*args, **kwargs)


_StaticOutputInst = _StaticOutput()

print_info = _StaticOutputInst.print_info
print_debug = _StaticOutputInst.print_debug
print_error = _StaticOutputInst.print_error
print_exc = _StaticOutputInst.print_exc


def timing_start() -> list[float] | None:
    if DEBUG:
        return [timestamp()]

    return None


def timing_step(
    modulename: str, start: list[float] | None, label: str
) -> None:
    if DEBUG and start:
        cts = timestamp()
        print_debug(modulename, label, f"in {cts - start[0]:.6f} s")
        start[0] = cts