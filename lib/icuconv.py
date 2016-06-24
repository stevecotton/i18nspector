# Copyright © 2013 Jakub Wilk <jwilk@jwilk.net>
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
string encoding and decoding using PyICU
'''

import re
import sys

import icu

default_encoding = sys.getdefaultencoding()

_parse_decode_error = re.compile(r"^'\S+' codec can't decode byte 0x[0-9a-f]{2} in position ([0-9]+): [0-9]+ \((.+)\)$").match

def encode(input: str, encoding=default_encoding, errors='strict'):
    if not isinstance(input, str):
        raise TypeError('input must be str, not {tp}'.format(tp=type(input).__name__))
    if not isinstance(encoding, str):
        raise TypeError('encoding must be str, not {tp}'.format(tp=type(encoding).__name__))
    if not isinstance(errors, str):
        raise TypeError('errors must be str, not {tp}'.format(tp=type(errors).__name__))
    if len(input) == 0:
        return b''
    if errors != 'strict':
        raise NotImplementedError('error handler {e!r} is not implemented'.format(e=errors))
    return _encode(input, encoding=encoding)

def _encode(input: str, *, encoding):
    s = icu.UnicodeString(input).encode(encoding)
    try:
        _decode(s, encoding=encoding)
    except UnicodeDecodeError as exc:
        # PyICU uses the default error callback
        # (UCNV_FROM_U_CALLBACK_SUBSTITUTE), which is not what we want, and doesn't even guarantee that the resulting string is correctly encoded. As a
        # work-around, try to decode
        raise UnicodeEncodeError(encoding, input, 0, len(input), exc.args[-1])
    return s

def decode(input: bytes, encoding=default_encoding, errors='strict'):
    if not isinstance(input, bytes):
        raise TypeError('input must be bytes, not {tp}'.format(tp=type(input).__name__))
    if not isinstance(encoding, str):
        raise TypeError('encoding must be str, not {tp}'.format(tp=type(encoding).__name__))
    if not isinstance(errors, str):
        raise TypeError('errors must be str, not {tp}'.format(tp=type(errors).__name__))
    if len(input) == 0:
        return ''
    if errors != 'strict':
        raise NotImplementedError('error handler {e!r} is not implemented'.format(e=errors))
    return _decode(input, encoding=encoding)

def _decode(input: bytes, *, encoding):
    try:
        s = icu.UnicodeString(input, encoding)
    except ValueError as exc:
        begin = 0
        end = len(input)
        message = str(exc)
        match = _parse_decode_error(message)
        if match is not None:
            begin, message = match.groups()
            begin = int(begin)
            end = begin + 1
        raise UnicodeDecodeError(encoding, input, begin, end, message)
    else:
        return str(s)

__all__ = ['encode', 'decode']

# vim:ts=4 sw=4 et
