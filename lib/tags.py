# Copyright © 2012 Jakub Wilk <jwilk@jwilk.net>
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

import functools
import os
import configparser

from . import misc

@functools.total_ordering
class OrderedObject(object):

    _parent = None

    def __init__(self, name, value):
        assert self._parent is not None
        self._name = name
        self._value = value

    def __lt__(self, other):
        if not isinstance(other, OrderedObject):
            return NotImplemented
        if self._parent is not other._parent:
            return NotImplemented
        return self._value < other._value

    def __eq__(self, other):
        if not isinstance(other, OrderedObject):
            return NotImplemented
        if self._parent is not other._parent:
            return NotImplemented
        return self._value == other._value

    def __hash__(self):
        return hash(self._value)

    def __str__(self):
        return str(self._name)

    def __repr__(self):
        return '<OrderedObject: {!r}>'.format(self._name)

class OrderedGroup(object):

    def __init__(self, name, *items):
        self._child_type = ct = type(name, (OrderedObject,), dict(_parent=self))
        self._objects = dict(
            (name, ct(name, value))
            for value, name in enumerate(items)
        )

    def __getitem__(self, name):
        return self._objects[name]

    def __repr__(self):
        return '<OrderedGroup({type!r}): {objects}>'.format(
            type=self._child_type.__name__,
            objects=', '.join(self._objects.keys())
        )

severities = OrderedGroup('Severity',
    'pedantic',
    'wishlist',
    'minor',
    'normal',
    'important',
    'serious'
)

certainties = OrderedGroup('Certainty',
    'wild-guess',
    'possible',
    'certain',
)

class Tag(object):

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            getattr(self, '_set_' + k)(v)
        self.name, self.severity, self.certainty

    def _set_name(self, value):
        self.name = value

    def _set_severity(self, value):
        self.severity = severities[value]

    def _set_certainty(self, value):
        self.certainty = certainties[value]

    def get_colors(self):
        prio = self.get_priority()
        n = dict(
            P=2,
            I=6,
            W=3,
            E=1,
        )[prio]
        return ('\x1b[3{}m'.format(n), '\x1b[0m')

    def get_priority(self):
        s = self.severity
        S = severities
        c = self.certainty
        C = certainties
        return {
            S['pedantic']: 'P',
            S['wishlist']: 'I',
            S['minor']: 'IW'[c >= C['certain']],
            S['normal']: 'IW'[c >= C['possible']],
            S['important']: 'EW'[c >= C['possible']],
            S['serious']: 'E',
        }[s]

    def format(self, target, *extra, color=False):
        if color:
            color_on, color_off = self.get_colors()
        else:
            color_on = color_off = ''
        s = '{prio}: {target}: {on}{tag}{off}'.format(
            prio=self.get_priority(),
            target=target,
            tag=self.name,
            on=color_on,
            off=color_off,
        )
        if extra:
            s += ' ' + ' '.join(map(str, extra))
        return s

class TagInfo(object):

    def __init__(self, datadir):
        path = os.path.join(datadir, 'tags')
        cp = configparser.ConfigParser(interpolation=None, default_section='')
        cp.read(path, encoding='UTF-8')
        if not misc.is_sorted(cp):
            raise configparser.ParsingError('sections are not sorted')
        self._tags = {}
        for tagname, section in cp.items():
            if not tagname:
                continue
            kwargs = dict(section.items())
            kwargs['name'] = tagname
            self._tags[tagname] = Tag(**kwargs)

    def __getitem__(self, tagname):
        return self._tags[tagname]

# vim:ts=4 sw=4 et
