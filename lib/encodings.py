# Copyright © 2012-2021 Jakub Wilk <jwilk@jwilk.net>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''
- encoding information registry
- codecs for unusual encodings
'''

import codecs
import configparser
import encodings.aliases as encoding_aliases
import functools
import itertools
import os
import unicodedata
import sys

from lib import iconv
from lib import misc
from lib import paths

class EncodingLookupError(LookupError):

    def __init__(self, encoding):
        LookupError.__init__(self, 'unknown encoding: ' + encoding)

def _not_implemented(*args, **kwargs):  # no coverage
    del args, kwargs
    raise NotImplementedError

def charmap_encoding(encoding):

    def encode(input, errors='strict'):
        return codecs.charmap_encode(input, errors, encoding_table)

    def decode(input, errors='strict'):
        return codecs.charmap_decode(input, errors, decoding_table)

    path = os.path.join(paths.datadir, 'charmaps', encoding.upper())
    try:
        file = open(path, 'rb')  # pylint: disable=consider-using-with
    except FileNotFoundError:
        raise EncodingLookupError(encoding)
    with file:
        decoding_table = file.read()
    decoding_table = decoding_table.decode('UTF-8')
    encoding_table = codecs.charmap_build(decoding_table)

    return codecs.CodecInfo(
        encode=encode,
        decode=decode,
        streamreader=_not_implemented,
        streamwriter=_not_implemented,
        incrementalencoder=_not_implemented,
        incrementaldecoder=_not_implemented,
        name=encoding,
    )

def iconv_encoding(encoding):

    def encode(input, errors='strict'):
        output = iconv.encode(input, encoding=encoding, errors=errors)
        return output, len(input)

    def decode(input, errors='strict'):
        output = iconv.decode(bytes(input), encoding=encoding, errors=errors)
        return output, len(input)

    return codecs.CodecInfo(
        encode=encode,
        decode=decode,
        streamreader=_not_implemented,
        streamwriter=_not_implemented,
        incrementalencoder=_not_implemented,
        incrementaldecoder=_not_implemented,
        name=encoding,
    )

_interesting_ascii_bytes = bytes(itertools.chain([
    0,  # NUL
    4,  # EOT
    7,  # BEL
    8,  # BS
    9,  # HT
    10,  # LF
    11,  # VT
    12,  # FF
    13,  # CR
    27,  # ESC
], range(32, 127)))
_interesting_ascii_str = _interesting_ascii_bytes.decode()

def _read_encodings():
    path = os.path.join(paths.datadir, 'encodings')
    cp = configparser.ConfigParser(interpolation=None, default_section='')
    cp.read(path, encoding='UTF-8')
    e2c = {}
    c2e = {}
    for encoding, extra in cp['portable-encodings'].items():
        e2c[encoding] = None
        if extra == '':
            pycodec = codecs.lookup(encoding)
            e2c[encoding] = pycodec
            c2e.setdefault(pycodec.name, encoding)
        elif extra == 'not-python':
            pass
        else:
            raise misc.DataIntegrityError
    extra_encodings = {
        key.lower()
        for key, value in cp['extra-encodings'].items()
    }
    return (e2c, c2e, extra_encodings)

[_portable_encodings, _pycodec_to_encoding, _extra_encodings] = _read_encodings()
_unmangle_encoding = {}
if sys.version_info >= (3, 9):
    # Python >= 3.9 replaces hyphens with underscores:
    # https://bugs.python.org/issue39337
    # We need to undo this damage.
    for _key in set(_portable_encodings) | set(_extra_encodings):
        assert _key not in _unmangle_encoding
        _unmangle_encoding[_key.replace('-', '_')] = _key
    _key = None
    del _key

def _read_control_characters():
    path = os.path.join(paths.datadir, 'control-characters')
    cp = configparser.ConfigParser(interpolation=None, default_section='')
    cp.read(path, encoding='UTF-8')
    for section in cp.values():
        if not section.name:
            continue
        misc.check_sorted(section)
        for code, name in section.items():
            if len(code) != 2:
                raise misc.DataIntegrityError
            code = chr(int(code, 16))
            if unicodedata.category(code) != 'Cc':
                raise misc.DataIntegrityError
            if name.upper() != name:
                raise misc.DataIntegrityError
            yield (code, name)

_control_character_names = dict(_read_control_characters())

def get_portable_encodings(python=True):
    return (
        encoding
        for encoding, codec in _portable_encodings.items()
        if (not python) or (codec is not None)
    )

def is_portable_encoding(encoding, python=True):
    encoding = encoding.lower()
    if encoding.startswith('iso_'):
        encoding = 'iso-' + encoding[4:]
    if python:
        return _portable_encodings.get(encoding, None) is not None
    else:
        return encoding in _portable_encodings

def propose_portable_encoding(encoding, python=True):
    del python  # never used; only encodings supported by Python are proposed
    try:
        pycodec = codecs.lookup(encoding)
        new_encoding = _pycodec_to_encoding[pycodec.name]
    except LookupError:
        return
    assert is_portable_encoding(new_encoding, python=True)
    return new_encoding.upper()

def is_ascii_compatible_encoding(encoding, *, missing_ok=True):
    try:
        decoded_ascii_bytes = _interesting_ascii_bytes.decode(encoding)
        if isinstance(decoded_ascii_bytes, bytes):
            # may happen on PyPy for non-text encodings
            raise RuntimeError
        return decoded_ascii_bytes == _interesting_ascii_str
    except UnicodeDecodeError:
        return False
    except LookupError:
        pass
    except Exception:  # no coverage; pylint: disable=broad-except
        pass
    if missing_ok:
        return False
    else:
        raise EncodingLookupError(encoding)

def _codec_search_function(encoding):
    encoding = _unmangle_encoding.get(encoding, encoding)
    if _portable_encodings.get(encoding, False) is None:
        # portable according to gettext documentation
        # but not supported directly by Python
        pass
    elif encoding in _extra_encodings:
        # non-portable, but used by real-world software
        pass
    else:
        return
    try:
        return charmap_encoding(encoding)
    except EncodingLookupError:
        return iconv_encoding(encoding)

@functools.lru_cache(maxsize=1)
def install_extra_encodings():
    codecs.register(_codec_search_function)
    for enc_name in _portable_encodings:
        if enc_name.startswith('iso-'):
            suffix = enc_name[4:].replace('-', '_')
            encoding_aliases.aliases.setdefault(suffix, 'iso' + suffix)

def get_character_name(ch):
    try:
        return unicodedata.name(ch)
    except ValueError:
        if unicodedata.category(ch) == 'Cn':
            return 'non-character'
        name = _control_character_names.get(ch)
        if name is None:
            raise
        return 'control character ' + name

# vim:ts=4 sts=4 sw=4 et
