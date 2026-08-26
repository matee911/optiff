"""Presence and size of the metadata blocks embedded in a TIFF."""

from __future__ import annotations

from optiff.document import TiffDocument
from optiff.units import format_size


class MetadataAnalyzer:
    def __init__(self, document: TiffDocument):
        self.document = document

    def report(self) -> dict[str, str]:
        return {
            "XMP": self._size(700),
            # These two lines describe XMP only. Photoshop keeps the
            # Content Credentials marker in the `CAI ` block of
            # ImageSourceData, which the PROVENANCE section covers.
            "Content Credentials in XMP": self._content_credentials(),
            "C2PA in XMP": self._c2pa_in_xmp(),
            "Photoshop Image Resources": self._size(34377),
            "Photoshop ImageSourceData": self._size(37724),
            "IPTC": self._size(33723),
            "ICC Profile": self._size(34675),
            "EXIF": self._exif(),
        }

    def _size(self, tag_number: int) -> str:
        tag = self.document.tag(tag_number)

        if tag is None:
            return "NOT FOUND"

        return format_size(self.document.tag_value_size(tag))

    def _xmp(self) -> bytes:
        return self.document.raw_tag_data(700) or b""

    def _content_credentials(self) -> str:
        data = self._xmp().lower()

        if b"contentcredentials" in data or b"content-credentials" in data:
            return "FOUND"

        return "NO / not detected"

    def _c2pa_in_xmp(self) -> str:
        return "FOUND" if b"c2pa" in self._xmp().lower() else "NO"

    def _exif(self) -> str:
        return "FOUND" if self.document.tag(34665) is not None else "NOT FOUND"
