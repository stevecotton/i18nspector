# Copyright © 2021 Steve Cotton <steve@octalot.co.uk>
# Copyright © 2016-2020 Jakub Wilk <jwilk@jwilk.net>
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
Wesnoth Markup Language's variable-substitutions
'''

import re

_field_re = re.compile(r'''
    \$ (?P<name> [\w][\.\w]+ )
''', re.VERBOSE)

def _printable_prefix(s, r=re.compile('[ -\x7E]+')):
    return r.match(s).group()

class Error(Exception):
    message = 'invalid placeholder specification'

class FormatString():

    def __init__(self, s):
        #print("WML check parsing:", s)
        self._items = items = []
        arguments = set()
        for match in _field_re.finditer(s):
            items += [match.group()]
            argname = match.group('name')
            if argname is not None:
                arguments.add(argname)
        self.arguments = frozenset(arguments)
        #if self.arguments:
            #print("Found arguments:", self.arguments)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

# vim:ts=4 sts=4 sw=4 et

