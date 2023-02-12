def _unicode_truncate(ustr: str, length: int, encoding: str = "UTF-8") -> str:
    "Truncate @ustr to specific encoded byte length"
    bstr = ustr.encode(encoding)[:length]
    return bstr.decode(encoding, "ignore")


def _split_first_line(text: str) -> tuple[str, str]:
    """Take first non-empty line of @text and rest"""
    lines, *rest = text.lstrip().split("\n", maxsplit=1)
    return lines, rest[0] if rest else ""


def _split_first_words(text: str, maxlen: int) -> tuple[str, str]:
    text = text.lstrip()
    first_text = _unicode_truncate(text, maxlen)
    words = first_text.split()
    if len(words) > 3:
        # TODO: why skip last world here and below? wrong indent?
        words = words[:-1]
        first_words = " ".join(words[:-1])
        if text.startswith(first_words):
            first_text = first_words

    rest_text = text[len(first_text) :]
    return first_text, rest_text


def extract_title_body(text: str, maxtitlelen: int = 60) -> tuple[str, str]:
    """Prepare @text: Return a (title, body) tuple

    @text: A user-submitted paragraph or otherwise snippet of text. We
    try to detect an obvious title and then return the title and the
    following body. Otherwise we extract a title from the first words,
    and return the full text as body.

    @maxtitlelen: A unitless measure of approximate length of title.
    The default value yields a resulting title of approximately 60 ascii
    characters, or 20 asian characters.

    >>> extract_title_body("Short Text")
    ('Short Text', '')

    >>> title, body = extract_title_body(u"執筆方針については、項目名の付け方、"
    ...     "フォーマットや表記上の諸問題に関して多くの方針が存在している。")
    >>> print(title)
    執筆方針については、項目名の付け方、フォ
    >>> print(body)         # doctest: +ELLIPSIS
    執筆方針については、項目名の付け方、フォ...して多くの方針が存在している。
    """
    # if you don't make real tests, it's not not worth doing it at all.

    if not text.strip():
        return text, ""

    firstline, rest = _split_first_line(text)
    if len(firstline.encode("UTF-8")) <= maxtitlelen:
        return firstline, rest

    # We use the UTF-8 encoding and truncate due to it:
    # this is a good heuristic for ascii vs "wide characters"
    # it results in taking fewer characters if they are asian, which
    # is exactly what we want
    firstline, rest = _split_first_words(text, maxtitlelen)

    if rest.strip():
        return firstline, text

    return text, ""


if __name__ == "__main__":
    import doctest

    doctest.testmod()