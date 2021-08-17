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
message format checks: Wesnoth Markup Language's variable-substitutions
'''

from lib import tags

from lib.check.msgformat import Checker as CheckerBase
from lib.check.msgrepr import message_repr

from lib.strformat import wmlvar as backend

class Checker(CheckerBase):

    backend = backend  # pylint: disable=self-assigning-variable

    def check_string(self, ctx, message, s):
        return backend.FormatString(s)

    def check_args(self, message, src_loc, src_fmt, dst_loc, dst_fmt, *, omitted_int_conv_ok=False):
        def sort_key(item):
            return (isinstance(item, str), item)
        prefix = message_repr(message, template='{}:')
        src_args = src_fmt.arguments
        dst_args = dst_fmt.arguments
        for key in sorted(dst_args - src_args, key=sort_key):
            self.tag('wml-variables-format-string-unknown-variable', prefix, key,
                tags.safestr('in'), tags.safestr(dst_loc),
                tags.safestr('but not in'), tags.safestr(src_loc),
            )
        missing_keys = src_args - dst_args
        if len(missing_keys) == 1 and omitted_int_conv_ok:
            missing_keys = ()
        for key in sorted(missing_keys):
            self.tag('wml-variables-format-string-missing-argument', prefix, key,
                tags.safestr('not in'), tags.safestr(dst_loc),
                tags.safestr('while in'), tags.safestr(src_loc),
            )

    def check_message(self, ctx, message, flags):
        #print ("\n\ncheck_message", message)
        return CheckerBase.check_message(self, ctx, message, flags)

if __name__ == "__main__":
    print("This is a plug-in for i18nspector, it isn't meant to be run standalone")
