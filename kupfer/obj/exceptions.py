from kupfer import kupferstring
from kupfer.obj.base import OperationError


class LocaleOperationError(OperationError):
    """
    User-visible error created from locale-encoded
    error message (for example OSError)
    """

    def __init__(self, err: bytes):
        OperationError.__init__(self, kupferstring.fromlocale(err))


class NotAvailableError(OperationError):
    """
    User-visible error message when an external
    tool is the wrong version
    """

    def __init__(self, toolname: str):
        OperationError.__init__(
            self, _("%s does not support this operation") % toolname
        )


class NoMultiError(OperationError):
    def __init__(self):
        OperationError.__init__(
            self, _("Can not be used with multiple objects")
        )
