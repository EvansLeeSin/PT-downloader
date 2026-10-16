import hashlib

import pytest

from app.errors import AppError
from app.torrent import identify_torrent
from tests.conftest import bencode


def test_v1(torrent_bytes, torrent_hash):
    identity = identify_torrent(torrent_bytes)
    assert identity.name == "sample"
    assert identity.hashes == {torrent_hash}


def test_raw_info_is_not_reencoded():
    # Valid, deliberately non-canonical dictionary order.
    raw = b"d4:name6:sample6:lengthi1e6:pieces20:" + b"x" * 20 + b"12:piece lengthi16384ee"
    data = b"d4:info" + raw + b"e"
    assert identify_torrent(data).hashes == {hashlib.sha1(raw).hexdigest()}


def test_v2_and_hybrid():
    fields = {b"name": b"sample", b"meta version": 2, b"file tree": {}, b"piece length": 16384}
    digest = hashlib.sha256(bencode(fields)).hexdigest()
    assert identify_torrent(bencode({b"info": fields})).hashes == {digest, digest[:40]}
    fields.update({b"pieces": b"x" * 20, b"length": 1})
    assert len(identify_torrent(bencode({b"info": fields})).hashes) == 3


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html>login</html>",
        b'{"error":"denied"}',
        b"d4:infoi1ee",
        b"de",
        b"d4:info",
        b"d1:ai01ee",
        b"d1:ai1e1:ai2ee",
        b"l" * 70 + b"e" * 70,
    ],
)
def test_invalid_torrents(content):
    with pytest.raises(AppError) as error:
        identify_torrent(content)
    assert error.value.code == "INVALID_TORRENT"
