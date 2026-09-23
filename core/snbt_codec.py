from __future__ import annotations

"""Small, lossless SNBT editor for Minecraft's newline-separated SNBT files.

Minecraft's SNBT writer commonly omits commas between compound fields when
fields are separated by newlines.  nbtlib's strict parser intentionally does
not accept that dialect, so this module parses the structure and keeps the
original text.  Only explicitly selected string values or compound keys are
rewritten; unknown fields and formatting remain byte-for-byte unchanged.
"""

import json
from dataclasses import dataclass, field
from typing import Any


class SnbtParseError(ValueError):
    """Raised when an SNBT document is structurally invalid."""


@dataclass(slots=True, frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int


@dataclass(slots=True)
class _Scalar:
    token: _Token


@dataclass(slots=True)
class SnbtField:
    key: str
    key_token: _Token
    value: Any


@dataclass(slots=True)
class SnbtCompound:
    start: int
    end: int
    fields: list[SnbtField] = field(default_factory=list)

    def field(self, key: str) -> SnbtField | None:
        for item in self.fields:
            if item.key == key:
                return item
        return None


@dataclass(slots=True)
class SnbtList:
    start: int
    end: int
    values: list[Any] = field(default_factory=list)


def _decode_string(token: _Token) -> str:
    if token.kind != "string":
        raise SnbtParseError("Expected a quoted SNBT string")
    if token.text.startswith('"'):
        try:
            return str(json.loads(token.text))
        except json.JSONDecodeError as exc:
            raise SnbtParseError(f"Invalid quoted SNBT string: {exc}") from exc
    body = token.text[1:-1]
    return bytes(body, "utf-8").decode("unicode_escape")


def _encode_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def _skip_space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _token(self) -> _Token:
        self._skip_space()
        if self.index >= len(self.text):
            raise SnbtParseError("Unexpected end of SNBT")
        start = self.index
        char = self.text[self.index]
        if char in "{}[],:;":
            self.index += 1
            return _Token(char, char, start, self.index)
        if char in "\"'":
            quote = char
            self.index += 1
            escaped = False
            while self.index < len(self.text):
                current = self.text[self.index]
                self.index += 1
                if escaped:
                    escaped = False
                    continue
                if current == "\\":
                    escaped = True
                elif current == quote:
                    return _Token("string", self.text[start : self.index], start, self.index)
            raise SnbtParseError("Unterminated SNBT string")

        while self.index < len(self.text):
            current = self.text[self.index]
            if current.isspace() or current in "{}[],:;":
                break
            self.index += 1
        if self.index == start:
            raise SnbtParseError(f"Unexpected SNBT character at {start}")
        return _Token("bare", self.text[start : self.index], start, self.index)

    def _peek(self) -> _Token:
        saved = self.index
        token = self._token()
        self.index = saved
        return token

    def parse(self) -> SnbtCompound:
        root = self._compound()
        self._skip_space()
        if self.index != len(self.text):
            raise SnbtParseError(f"Unexpected content after SNBT root at {self.index}")
        return root

    def _compound(self) -> SnbtCompound:
        opening = self._token()
        if opening.kind != "{":
            raise SnbtParseError(f"Expected '{{' at {opening.start}")
        fields: list[SnbtField] = []
        while True:
            token = self._peek()
            if token.kind == "}":
                closing = self._token()
                return SnbtCompound(opening.start, closing.end, fields)
            key_token = self._token()
            if key_token.kind not in {"bare", "string"}:
                raise SnbtParseError(f"Expected compound key at {key_token.start}")
            colon = self._token()
            if colon.kind != ":":
                raise SnbtParseError(f"Expected ':' after compound key at {colon.start}")
            value = self._value()
            fields.append(SnbtField(self._key(key_token), key_token, value))
            separator = self._peek()
            if separator.kind == ",":
                self._token()

    def _key(self, token: _Token) -> str:
        return _decode_string(token) if token.kind == "string" else token.text

    def _value(self) -> Any:
        token = self._peek()
        if token.kind == "{":
            return self._compound()
        if token.kind == "[":
            return self._list()
        if token.kind in {"bare", "string"}:
            return _Scalar(self._token())
        raise SnbtParseError(f"Expected SNBT value at {token.start}")

    def _list(self) -> SnbtList:
        opening = self._token()
        if opening.kind != "[":
            raise SnbtParseError(f"Expected '[' at {opening.start}")
        values: list[Any] = []
        # Typed arrays use [I;, [B; or [L;.  The marker is syntax, not a value.
        marker = self._peek()
        if marker.kind == "bare" and marker.text in {"B", "I", "L"}:
            self._token()
            semicolon = self._peek()
            if semicolon.kind == ";":
                self._token()
        while True:
            token = self._peek()
            if token.kind == "]":
                closing = self._token()
                return SnbtList(opening.start, closing.end, values)
            values.append(self._value())
            separator = self._peek()
            if separator.kind == ",":
                self._token()


class SnbtDocument:
    def __init__(self, text: str, root: SnbtCompound) -> None:
        self.text = text
        self.root = root
        self._replacements: list[tuple[int, int, str]] = []

    def _field(self, compound: SnbtCompound, key: str) -> SnbtField:
        field = compound.field(key)
        if field is None:
            raise KeyError(f"SNBT field not found: {key}")
        return field

    def string_value(self, field: SnbtField) -> str:
        if not isinstance(field.value, _Scalar) or field.value.token.kind != "string":
            raise SnbtParseError(f"SNBT field {field.key} is not a quoted string")
        return _decode_string(field.value.token)

    def compound_value(self, field: SnbtField) -> SnbtCompound:
        if not isinstance(field.value, SnbtCompound):
            raise SnbtParseError(f"SNBT field {field.key} is not a compound")
        return field.value

    def root_field(self, key: str) -> SnbtField:
        return self._field(self.root, key)

    def replace_string(self, field: SnbtField, expected: str, replacement: str) -> None:
        if self.string_value(field) != expected:
            raise ValueError(
                f"SNBT field {field.key!r} has an unexpected value: "
                f"{self.string_value(field)!r}"
            )
        token = field.value.token
        self._replacements.append((token.start, token.end, _encode_string(replacement)))

    def replace_key(self, field: SnbtField, expected: str, replacement: str) -> None:
        actual = field.key
        if actual != expected:
            raise ValueError(f"SNBT key mismatch: expected {expected!r}, got {actual!r}")
        token = field.key_token
        replacement_text = _encode_string(replacement) if token.kind == "string" else replacement
        self._replacements.append((token.start, token.end, replacement_text))

    def render(self) -> str:
        result = self.text
        for start, end, replacement in sorted(self._replacements, reverse=True):
            result = result[:start] + replacement + result[end:]
        return result


def parse_snbt(text: str) -> SnbtDocument:
    return SnbtDocument(text, _Parser(text).parse())


def direct_compound_entries(document: SnbtDocument, key: str) -> list[SnbtField]:
    return document.compound_value(document.root_field(key)).fields
