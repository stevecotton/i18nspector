# Copyright © 2012, 2013 Jakub Wilk <jwilk@jwilk.net>
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
gettext header support:
- header field names registry
- date parser
- plural expressions parser
'''

import configparser
import datetime
import functools
import os
import re

from . import misc
from . import intexpr

class GettextInfo(object):

    def __init__(self, datadir):
        path = os.path.join(datadir, 'header-fields')
        with open(path, 'rt', encoding='ASCII') as file:
            fields = [
                s.rstrip() for s in file
                if s.rstrip() and not s.startswith('#')
            ]
        misc.check_sorted(fields)
        self.header_fields = frozenset(fields)
        path = os.path.join(datadir, 'timezones')
        cp = configparser.ConfigParser(interpolation=None, default_section='')
        cp.optionxform = lambda x: x
        cp.read(path, encoding='ASCII')
        self.timezones = {
            abbrev: offsets.split()
            for abbrev, offsets in cp['timezones'].items()
        }
        tz_re = '|'.join(re.escape(tz) for tz in self.timezones)
        self._parse_date = re.compile('''
            ^ \s*
            ( [0-9]{4}-[0-9]{2}-[0-9]{2} )  # YYYY-MM-DD
            (?: \s+ | T )
            ( [0-9]{2}:[0-9]{2} )  # hh:mm
            (?: : [0-9]{2} )?  # ss
            \s*
            (?:
              (?: GMT | UTC )? ( [+-] [0-9]{2} ) :? ( [0-9]{2} )  # ZZzz
            | [+]? (''' + tz_re + ''')
            ) ?
            \s* $
        ''', re.VERBOSE).match

    def fix_date_format(self, s, *, tz_hint=None):
        if tz_hint is not None:
            datetime.datetime.strptime(tz_hint, '%z')  # just check syntax
        match = self._parse_date(s)
        if match is None:
            return
        (date, time, zhour, zminute, zabbr) = match.groups()
        if (zhour is not None) and (zminute is not None):
            zone = zhour + zminute
        elif zabbr is not None:
            try:
                [zone] = self.timezones[zabbr]
            except ValueError:
                return
        elif tz_hint is not None:
            zone = tz_hint
        else:
            return
        s = '{} {}{}'.format(date, time, zone)
        assert len(s) == 21, 'len({!r}) != 21'.format(s)
        try:
            parse_date(s)
        except DateSyntaxError:
            return
        return s

# ==========================
# Header and message parsing
# ==========================

is_valid_field_name = re.compile(r'^[\x21-\x39\x3b-\x7e]+$').match
# http://tools.ietf.org/html/rfc5322#section-3.6.8

def parse_header(s):
    lines = s.split('\n')
    if lines[-1] == '':
        lines.pop()
    for line in lines:
        key, *values = line.split(':', 1)
        if values and is_valid_field_name(key):
            assert len(values) == 1
            value = values[0].lstrip(' \t')
            yield {key: value}
        else:
            yield line

search_for_conflict_marker = re.compile(r'^#-#-#-#-#  .+  #-#-#-#-#$', re.MULTILINE).search
# http://git.savannah.gnu.org/cgit/gettext.git/tree/gettext-tools/src/msgl-cat.c?id=v0.18.2.1#n590
# http://www.gnu.org/software/gettext/manual/html_node/Creating-Compendia.html#Creating-Compendia

# ================
# Plurals handling
# ================

# http://git.savannah.gnu.org/cgit/gettext.git/tree/gettext-runtime/intl/plural.y?id=v0.18.2.1#n132

class PluralFormsSyntaxError(Exception):
    pass

class PluralExpressionSyntaxError(PluralFormsSyntaxError):
    pass

_plural_exp_tokens = [
    (r'[0-9]+', None),
    (r'[=!]=', None),
    (r'!', 'not'),
    (r'&&', 'and'),
    (r'[|][|]', 'or'),
    (r'[<>]=?', None),
    (r'[*/%]', None),
    (r'[+-]', None),
    (r'n', None),
    (r'[?:]', None),
    (r'[()]', None),
    (r'[ \t]', None),
    (r'.', '_'),  # junk
]

_plural_exp_token_re = '|'.join(
    chunk if repl is None else '(?P<{}>{})'.format(repl, chunk)
    for chunk, repl in _plural_exp_tokens
)
_plural_exp_token_re = re.compile(_plural_exp_token_re)

def _plural_exp_tokenize(s):
    for match in _plural_exp_token_re.finditer(s):
        for pytoken, ctoken in match.groupdict().items():
            if ctoken is not None:
                break
        if ctoken is not None:
            if pytoken == '_':  # junk
                raise PluralExpressionSyntaxError(match.group(0))
            yield ' {} '.format(pytoken)
        else:
            yield match.group(0)

_ifelse_re = re.compile(r'(.*?)[?](.*?):(.*)')
def _subst_ifelse(match):
    return '({true} if {cond} else {false})'.format(
        cond=match.group(1),
        true=match.group(2),
        false=_subst_ifelse(match.group(3))
    )
# The magic below makes _subst_ifelse() act on strings rather than match
# objects.
_subst_ifelse = functools.partial(_ifelse_re.sub, _subst_ifelse)

def parse_plural_expression(s):
    stack = ['']
    for token in _plural_exp_tokenize(s):
        if token == '(':
            stack += ['']
        elif token == ')':
            if len(stack) <= 1:
                raise PluralExpressionSyntaxError
            s = _subst_ifelse(stack.pop())
            stack[-1] += '({})'.format(s)
        else:
            stack[-1] += token
    if len(stack) != 1:
        raise PluralExpressionSyntaxError
    [s] = stack
    s = _subst_ifelse(s)
    try:
        fn = intexpr.Expression(s)
    except SyntaxError:
        raise PluralExpressionSyntaxError
    return fn

_parse_plural_forms = re.compile(r'^\s*nplurals=([1-9][0-9]*);\s*plural=([^;]+);?\s*$').match

def parse_plural_forms(s):
    match = _parse_plural_forms(s)
    if match is None:
        raise PluralFormsSyntaxError
    n = int(match.group(1), 10)
    expr = parse_plural_expression(match.group(2))
    return (n, expr)

# =====
# Dates
# =====

class DateSyntaxError(Exception):
    pass

def parse_date(s):
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M%z')
    except ValueError as exc:
        raise DateSyntaxError(exc)

epoch = datetime.datetime(1995, 7, 2, tzinfo=datetime.timezone.utc)

# vim:ts=4 sw=4 et
