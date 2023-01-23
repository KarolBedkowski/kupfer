from kupfer.core import settings


def is_known_terminal_executable(exearg: str) -> bool:
    setctl = settings.GetSettingsController()
    for _id, term in setctl.get_all_alternatives("terminal").items():
        if exearg == term["argv"][0]:
            return True

    return False


def get_configured_terminal() -> str:
    """
    Return the configured Terminal object
    """
    setctl = settings.GetSettingsController()
    return setctl.get_preferred_alternative("terminal")  # type: ignore
