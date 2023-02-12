#! /usr/bin/env python3

"""
Interfaces that may implements objects.
"""
import typing as ty


class TextRepresentation:
    """
    Kupfer Objects that implement this interface have a plain text
    representation that can be used for Copy & Paste etc
    """

    def get_text_representation(self) -> str:
        """The default implementation returns the represented object"""
        return str(self.object)  # pylint: disable=no-member


class UriListRepresentation:
    """
    Kupfer Objects that implement this interface have a uri-list
    representation that can be used for Copy & Paste etc

    get_urilist_representation should return a sequence of bytestring
    URIs.
    """

    def get_urilist_representation(self) -> list[ty.AnyStr]:
        """The default implementation raises notimplementederror"""
        raise NotImplementedError
