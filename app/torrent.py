"""Minimal bounded bencode reader. Hash the original info slice, never re-encode it."""

import hashlib
import re
from dataclasses import dataclass

from app.errors import AppError


@dataclass(frozen=True)
class TorrentIdentity:
    name: str
    hashes: frozenset[str]


class Reader:
    def __init__(self, content: bytes):
        self.content = content
        self.offset = 0
        self.nodes = 0
        self.info_slice = None

    def read(self, depth=0):
        self.nodes += 1
        if depth > 64 or self.nodes > 200_000 or self.offset >= len(self.content):
            raise ValueError("bencode bounds")
        marker = self.content[self.offset : self.offset + 1]
        if marker == b"i":
            end = self.content.index(b"e", self.offset + 1)
            text = self.content[self.offset + 1 : end]
            if len(text) > 24 or not re.fullmatch(rb"0|-?[1-9][0-9]*", text):
                raise ValueError("integer")
            self.offset = end + 1
            return int(text)
        if marker in (b"d", b"l"):
            self.offset += 1
            result = {} if marker == b"d" else []
            while self.content[self.offset : self.offset + 1] != b"e":
                if marker == b"d":
                    key = self.read(depth + 1)
                    if not isinstance(key, bytes) or key in result:
                        raise ValueError("dictionary key")
                    start = self.offset
                    result[key] = self.read(depth + 1)
                    if depth == 0 and key == b"info":
                        self.info_slice = self.content[start : self.offset]
                else:
                    result.append(self.read(depth + 1))
            self.offset += 1
            return result
        if marker.isdigit():
            end = self.content.index(b":", self.offset)
            digits = self.content[self.offset : end]
            if len(digits) > 9 or not re.fullmatch(rb"0|[1-9][0-9]*", digits):
                raise ValueError("length")
            start = end + 1
            self.offset = start + int(digits)
            if self.offset > len(self.content):
                raise ValueError("truncated string")
            return self.content[start : self.offset]
        raise ValueError("invalid marker")


def identify_torrent(content: bytes) -> TorrentIdentity:
    try:
        if not content or len(content) > 10 * 1024 * 1024:
            raise ValueError("size")
        reader = Reader(content)
        root = reader.read()
        if reader.offset != len(content) or not isinstance(root, dict):
            raise ValueError("root")
        info = root.get(b"info")
        raw = reader.info_slice
        if not isinstance(info, dict) or not raw or not isinstance(info.get(b"name"), bytes):
            raise ValueError("missing info")
        hashes = set()
        if b"pieces" in info:
            if not isinstance(info[b"pieces"], bytes) or len(info[b"pieces"]) % 20:
                raise ValueError("pieces")
            if b"length" not in info and not isinstance(info.get(b"files"), list):
                raise ValueError("missing length")
            hashes.add(hashlib.sha1(raw).hexdigest())
        if info.get(b"meta version") == 2:
            if not isinstance(info.get(b"file tree"), dict):
                raise ValueError("file tree")
            digest = hashlib.sha256(raw).hexdigest()
            hashes.update((digest, digest[:40]))
        if not hashes:
            raise ValueError("unsupported torrent")
        return TorrentIdentity(info[b"name"].decode("utf-8", "replace"), frozenset(hashes))
    except (ValueError, TypeError, IndexError, RecursionError) as exc:
        raise AppError(
            "INVALID_TORRENT", "站点返回的内容不是有效的 v1/v2 种子文件；未提交到下载器", 502
        ) from exc
