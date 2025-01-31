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
checks
'''

import abc
import collections
import difflib
import email.utils
import heapq
import os
import re
import types
import urllib.parse

import polib

from lib import domains
from lib import encodings as encinfo
from lib import gettext
from lib import ling
from lib import misc
from lib import polib4us
from lib import tags
from lib import xml

from lib.check.msgformat import c as msgformat_c
from lib.check.msgformat import perlbrace as msgformat_perlbrace
from lib.check.msgformat import pybrace as msgformat_pybrace
from lib.check.msgformat import python as msgformat_python
from lib.check.msgrepr import message_repr

class EnvironmentNotPatched(RuntimeError):
    pass

class EnvironmentAlreadyPatched(RuntimeError):
    pass

find_unusual_characters = re.compile(
    r'[\x00-\x08\x0B-\x1A\x1C-\x1F]'  # C0 except TAB, LF, ESC
    r'|\x1B(?!\[)'  # ESC, except when followed by [
    r'|\x7F'  # DEL
    r'|[\x80-\x9F]'  # C1
     '|\uFEFF'  # ZERO WIDTH NO-BREAK SPACE
     '|\uFFFD'  # REPLACEMENT CHARACTER
     '|[\uFFFE\uFFFF]'  # non-characters
    r'|(?<=\w)\xBF'  # INVERTED QUESTION MARK but only directly after a letter
).findall

header_fields_with_dedicated_checks = set()

def checks_header_fields(*fields):
    def identity(x):
        return x
    header_fields_with_dedicated_checks.update(fields)
    return identity

class Checker(metaclass=abc.ABCMeta):

    _patched_environment = None

    @classmethod
    def patch_environment(cls):
        if cls._patched_environment is not None:
            raise EnvironmentAlreadyPatched
        encinfo.install_extra_encodings()
        polib4us.install_patches()
        cls._patched_environment = True

    def __init__(self, path, *, options):
        if self._patched_environment is not True:
            raise EnvironmentNotPatched
        self.path = path
        self.fake_path = path
        if options.fake_root is not None:
            (real_root, fake_root) = options.fake_root
            if not real_root.endswith(os.sep):
                raise ValueError
            if not fake_root.endswith(os.sep):
                raise ValueError
            if path.startswith(real_root):
                self.fake_path = fake_root + path[len(real_root):]
        self.options = options
        self._message_format_checkers = {
            'c': msgformat_c.Checker(self),
            'perl-brace': msgformat_perlbrace.Checker(self),
            'python': msgformat_python.Checker(self),
            'python-brace': msgformat_pybrace.Checker(self),
        }

    @abc.abstractmethod
    def tag(self, tagname, *extra):
        pass

    def check(self):
        # If a file passed to polib doesn't exist, it will “helpfully” treat it
        # as PO/MO file _contents_. This is definitely not what we want. To
        # prevent such disaster, fail early if the file doesn't exit.
        try:
            os.stat(self.path)
        except OSError as exc:
            self.tag('os-error', tags.safestr(exc.strerror))
            return
        if self.options.file_type is None:
            extension = os.path.splitext(self.path)[-1]
        else:
            extension = '.' + self.options.file_type
        is_template = False
        is_binary = False
        if extension == '.po':
            constructor = polib.pofile
        elif extension == '.pot':
            constructor = polib.pofile
            is_template = True
        elif extension in {'.mo', '.gmo'}:
            constructor = polib.mofile
            is_binary = True
        else:
            self.tag('unknown-file-type')
            return
        broken_encoding = False
        try:
            try:
                file = constructor(self.path)
            except UnicodeDecodeError as exc:
                broken_encoding = exc
                file = constructor(self.path, encoding='ISO-8859-1')
        except polib4us.moparser.SyntaxError as exc:
            self.tag('invalid-mo-file', tags.safestr(exc))
            return
        except OSError as exc:
            message = str(exc)
            if exc.errno is not None:
                self.tag('os-error', tags.safestr(exc.strerror))
                return
            elif message.startswith('Syntax error in po file '):
                message = message[24:]
                message_parts = []
                if message.startswith(self.path + ' '):
                    message = message[len(self.path)+1:]
                match = re.match(r'^\(line ([0-9]+)\)(?:: (.+))?$', message)
                if match is not None:
                    lineno_part = 'line {}'.format(match.group(1))
                    message = match.group(2)
                    if message is not None:
                        lineno_part += ':'
                        if re.match(r'^[a-z]+( [a-z]+)*$', message):
                            message = tags.safestr(message)
                    message_parts += [tags.safestr(lineno_part)]
                if message is not None:
                    message_parts += [message]
                self.tag('syntax-error-in-po-file', *message_parts)
                return
            raise
        finally:
            if broken_encoding:
                # pylint: disable=no-member
                s = broken_encoding.object
                assert isinstance(s, bytes)
                begin = max(broken_encoding.start - 40, 0)
                end = broken_encoding.start + 40
                s = s[begin:end]
                self.tag('broken-encoding',
                    s,
                    tags.safestr('cannot be decoded as'),
                    broken_encoding.encoding.upper(),
                )
                # pylint: enable=no-member
                broken_encoding = True
        ctx = types.SimpleNamespace()
        ctx.file = file
        ctx.is_template = is_template
        ctx.is_binary = is_binary
        self.check_comments(ctx)
        self.check_headers(ctx)
        self.check_language(ctx)
        self.check_plurals(ctx)
        self.check_mime(ctx)
        if broken_encoding:
            ctx.encoding = None
        self.check_dates(ctx)
        self.check_project(ctx)
        self.check_translator(ctx)
        self.check_messages(ctx)

    def check_comments(self, ctx):
        regexs = {
            r'\bPACKAGE package\b',
            r'\bCopyright \S+ YEAR\b',
            r"\bTHE PACKAGE'S COPYRIGHT HOLDER\b",
        }
        if not ctx.is_template:
            regexs |= {
                r'\bFIRST AUTHOR\b',
                r'<EMAIL@ADDRESS>',
                r'(?<=>), YEAR\b',
            }
        regex = re.compile('|'.join(regexs))
        for line in ctx.file.header.splitlines():
            match = regex.search(line)
            if match is None:
                continue
            self.tag('boilerplate-in-initial-comments', line)

    @checks_header_fields('Language', 'X-Poedit-Language', 'X-Poedit-Country')
    def check_language(self, ctx):
        ctx.language = None
        duplicate_meta_language = False
        meta_languages = ctx.metadata['Language']
        if len(meta_languages) > 1:
            self.tag('duplicate-header-field-language')
            meta_languages = sorted(set(meta_languages))
            if len(meta_languages) > 1:
                duplicate_meta_language = True
        if len(meta_languages) == 1:
            [meta_language] = meta_languages
        else:
            meta_language = None
        orig_meta_language = meta_language
        if ctx.is_template:
            if meta_language is None:
                self.tag('no-language-header-field')
            return
        language = self.options.language
        language_source = 'command-line'
        language_source_quality = 1
        if language is None:
            path_components = os.path.normpath(self.path).split('/')
            try:
                i = path_components.index('LC_MESSAGES')
            except ValueError:
                i = 0
            if i > 0:
                language = path_components[i - 1]
                try:
                    language = ling.parse_language(language)
                    language.fix_codes()
                    language.remove_encoding()
                    language.remove_nonlinguistic_modifier()
                except ling.LanguageError:
                    # It's not our job to report possible errors in _pathnames_.
                    language = None
                else:
                    language_source = 'pathname'
            del path_components, i
        if language is None and self.path.endswith('.po'):
            language, ext = os.path.splitext(os.path.basename(self.path))
            assert ext == '.po'
            try:
                language = ling.parse_language(language)
                if language.encoding is not None:
                    # It's very likely that something else has been confused
                    # for the apparent encoding.
                    raise ling.LanguageError
                language.fix_codes()
                language.remove_nonlinguistic_modifier()
            except ling.LanguageError:
                # It's not our job to report possible errors in _pathnames_.
                language = None
            else:
                language_source = 'pathname'
                language_source_quality = 0
        if meta_language:
            try:
                meta_language = ling.parse_language(meta_language)
            except ling.LanguageError:
                try:
                    new_meta_language = ling.get_language_for_name(meta_language)
                except LookupError:
                    new_meta_language = None
                if new_meta_language:
                    self.tag('invalid-language', orig_meta_language, '=>', new_meta_language)
                else:
                    self.tag('invalid-language', orig_meta_language)
                meta_language = new_meta_language
        if meta_language:
            if meta_language.remove_encoding():
                self.tag('encoding-in-language-header-field', orig_meta_language)
            if meta_language.remove_nonlinguistic_modifier():
                self.tag('language-variant-does-not-affect-translation', orig_meta_language)
            try:
                if meta_language.fix_codes():
                    self.tag('invalid-language', orig_meta_language, '=>', meta_language)
            except ling.LanguageError:
                self.tag('invalid-language', orig_meta_language)
                meta_language = None
            if language_source_quality <= 0 and (
                '/{lang}/'.format(lang=meta_language) in self.path or
                '/{lang}/'.format(lang=str(meta_language).replace('_', '-')) in self.path
            ):
                # For LibreOffice, PO basename does not designate translation
                # language, but one of the path components does.
                # For example,
                #   translations/source/da/dictionaries/pl_PL.po
                # is a Danish translation.
                language = None
        if meta_language:
            if language is None:
                language = meta_language
                language_source = 'Language header field'
            elif language != meta_language:
                self.tag('language-disparity',
                    language, tags.safestr('({})'.format(language_source)),
                    '!=',
                    meta_language, tags.safestr('(Language header field)')
                )
        poedit_languages = ctx.metadata['X-Poedit-Language']
        if len(poedit_languages) > 1:
            self.tag('duplicate-header-field-x-poedit', 'X-Poedit-Language')
            poedit_languages = sorted(set(poedit_languages))
        poedit_countries = ctx.metadata['X-Poedit-Country']
        if len(poedit_countries) > 1:
            self.tag('duplicate-header-field-x-poedit', 'X-Poedit-Country')
            poedit_countries = sorted(set(poedit_countries))
        if len(poedit_languages) == 1 and len(poedit_countries) <= 1:
            [poedit_language] = poedit_languages
            # FIXME: This should take also X-Poedit-Country into account.
            try:
                poedit_language = ling.get_language_for_name(poedit_language)
            except LookupError:
                self.tag('unknown-poedit-language', poedit_language)
            else:
                if language is None:
                    language = poedit_language
                    language_source = 'X-Poedit-Language header field'
                elif language.language_code != poedit_language.language_code:
                    self.tag('language-disparity',
                        language, tags.safestr('({})'.format(language_source)),
                        '!=',
                        poedit_language, tags.safestr('(X-Poedit-Language header field)')
                    )
        if language is None:
            if not orig_meta_language and not duplicate_meta_language:
                self.tag('no-language-header-field')
            self.tag('unable-to-determine-language')
            return
        if not orig_meta_language and not duplicate_meta_language:
            self.tag('no-language-header-field', tags.safestr('Language:'), language)
        ctx.language = language

    @checks_header_fields('Plural-Forms')
    def check_plurals(self, ctx):
        ctx.plural_preimage = None
        plural_forms = ctx.metadata['Plural-Forms']
        if len(plural_forms) > 1:
            self.tag('duplicate-header-field-plural-forms')
            plural_forms = sorted(set(plural_forms))
            if len(plural_forms) > 1:
                return
        if len(plural_forms) == 1:
            [plural_forms] = plural_forms
        else:
            assert len(plural_forms) == 0
            plural_forms = None
        correct_plural_forms = None
        if ctx.language is not None:
            correct_plural_forms = ctx.language.get_plural_forms()
        has_plurals = False  # messages with plural forms (translated or not)?
        expected_nplurals = {}  # number of plurals in _translated_ messages
        for message in ctx.file:
            if message.obsolete:
                continue
            if message.msgid_plural is not None:
                has_plurals = True
                if not message.translated():
                    continue
                expected_nplurals[len(message.msgstr_plural)] = message
                if len(expected_nplurals) > 1:
                    break
        if len(expected_nplurals) > 1:
            args = []
            for n, message in sorted(expected_nplurals.items()):
                args += [n, message_repr(message, template='({})'), '!=']
            self.tag('inconsistent-number-of-plural-forms', *args[:-1])
        if ctx.is_template:
            plural_forms_hint = 'nplurals=INTEGER; plural=EXPRESSION;'
        elif correct_plural_forms:
            plural_forms_hint = tags.safe_format(
                ' or '.join('{}' for s in correct_plural_forms),
                *correct_plural_forms
            )
        else:
            plural_forms_hint = 'nplurals=<n>; plural=<expression>'
        if plural_forms is None:
            if has_plurals:
                if expected_nplurals:
                    self.tag('no-required-plural-forms-header-field', plural_forms_hint)
                else:
                    self.tag('no-plural-forms-header-field', plural_forms_hint)
            return
        if ctx.is_template:
            return
        try:
            (n, expr, ljunk, rjunk) = gettext.parse_plural_forms(plural_forms, strict=False)
        except gettext.PluralFormsSyntaxError:
            if has_plurals:
                self.tag('syntax-error-in-plural-forms', plural_forms, '=>', plural_forms_hint)
            else:
                self.tag('syntax-error-in-unused-plural-forms', plural_forms, '=>', plural_forms_hint)
            return
        if ljunk:
            self.tag('leading-junk-in-plural-forms', ljunk)
        if rjunk:
            self.tag('trailing-junk-in-plural-forms', rjunk)
        if len(expected_nplurals) == 1:
            [expected_nplurals] = expected_nplurals.keys()
            if n != expected_nplurals:
                self.tag('incorrect-number-of-plural-forms',
                    n, tags.safestr('(Plural-Forms header field)'), '!=',
                    expected_nplurals, tags.safestr('(number of msgstr items)')
                )
        locally_correct_n = locally_correct_expr = None
        if correct_plural_forms is not None:
            locally_correct_plural_forms = [
                (i, expression)
                for i, expression in map(gettext.parse_plural_forms, correct_plural_forms)
                if i == n
            ]
            if not locally_correct_plural_forms:
                if has_plurals:
                    self.tag('unusual-plural-forms', plural_forms, '=>', plural_forms_hint)
                else:
                    self.tag('unusual-unused-plural-forms', plural_forms, '=>', plural_forms_hint)
            elif len(locally_correct_plural_forms) == 1:
                [[locally_correct_n, locally_correct_expr]] = locally_correct_plural_forms
        plural_preimage = collections.defaultdict(list)
        unusual_plural_forms = False
        codomain_limit = 200
        try:
            for i in range(codomain_limit):
                fi = expr(i)
                if fi >= n:
                    message = tags.safe_format('f({}) = {} >= {}'.format(i, fi, n))
                    if has_plurals:
                        self.tag('codomain-error-in-plural-forms', message)
                    else:
                        self.tag('codomain-error-in-unused-plural-forms', message)
                    break
                plural_preimage[fi] += [i]
                if (n == locally_correct_n) and (fi != locally_correct_expr(i)) and (not unusual_plural_forms):
                    if has_plurals:
                        self.tag('unusual-plural-forms', plural_forms, '=>', plural_forms_hint)
                    else:
                        self.tag('unusual-unused-plural-forms', plural_forms, '=>', plural_forms_hint)
                    unusual_plural_forms = True
            else:
                ctx.plural_preimage = dict(plural_preimage)
        except OverflowError:
            message = tags.safe_format('f({}): integer overflow', i)
            if has_plurals:
                self.tag('arithmetic-error-in-plural-forms', message)
            else:
                self.tag('arithmetic-error-in-unused-plural-forms', message)
        except ZeroDivisionError:
            message = tags.safe_format('f({}): division by zero', i)
            if has_plurals:
                self.tag('arithmetic-error-in-plural-forms', message)
            else:
                self.tag('arithmetic-error-in-unused-plural-forms', message)
        codomain = expr.codomain()
        if codomain is not None:
            (x, y) = codomain
            uncov_rngs = []
            if x > 0:
                uncov_rngs += [range(0, x)]
            if y + 1 < n:
                uncov_rngs += [range(y + 1, n)]
        if (not uncov_rngs) and (ctx.plural_preimage is not None):
            period = expr.period()
            if period is None:
                period = (0, 1e999)
            if sum(period) < codomain_limit:
                for i in sorted(ctx.plural_preimage):
                    if (i > 0) and (i - 1 not in ctx.plural_preimage):
                        uncov_rngs += [range(i - 1, i)]
                        break
                    if (i + 1 < n) and (i + 1 not in ctx.plural_preimage):
                        uncov_rngs += [range(i + 1, i + 2)]
                        break
        for rng in uncov_rngs:
            rng = misc.format_range(rng, max=5)
            message = tags.safestr('f(x) != {}'.format(rng))
            if has_plurals:
                self.tag('codomain-error-in-plural-forms', message)
            else:
                self.tag('codomain-error-in-unused-plural-forms', message)
            ctx.plural_preimage = None

    @checks_header_fields('MIME-Version', 'Content-Transfer-Encoding', 'Content-Type')
    def check_mime(self, ctx):
        ctx.encoding = None
        # MIME-Version:
        mime_versions = ctx.metadata['MIME-Version']
        if len(mime_versions) > 1:
            self.tag('duplicate-header-field-mime-version')
            mime_versions = sorted(set(mime_versions))
        for mime_version in mime_versions:
            if mime_version != '1.0':
                self.tag('invalid-mime-version', mime_version, '=>', '1.0')
        if len(mime_versions) == 0:
            self.tag('no-mime-version-header-field', tags.safestr('MIME-Version: 1.0'))
        # Content-Transfer-Encoding:
        ctes = ctx.metadata['Content-Transfer-Encoding']
        if len(ctes) > 1:
            self.tag('duplicate-header-field-content-transfer-encoding')
            ctes = sorted(set(ctes))
        for cte in ctes:
            if cte != '8bit':
                self.tag('invalid-content-transfer-encoding', cte, '=>', '8bit')
        if len(ctes) == 0:
            self.tag('no-content-transfer-encoding-header-field', tags.safestr('Content-Transfer-Encoding: 8bit'))
        # Content-Type:
        cts = ctx.metadata['Content-Type']
        if len(cts) > 1:
            self.tag('duplicate-header-field-content-type')
            cts = sorted(set(cts))
        elif len(cts) == 0:
            content_type_hint = 'text/plain; charset=<encoding>'
            self.tag('no-content-type-header-field', tags.safestr('Content-Type: ' + content_type_hint))
            return
        encodings = set()
        for ct in cts:
            content_type_hint = 'text/plain; charset=<encoding>'
            match = re.search(r'(\Atext/plain; )?\bcharset=([^\s;]+)\Z', ct)
            if match:
                encoding = match.group(2)
                try:
                    is_ascii_compatible = encinfo.is_ascii_compatible_encoding(encoding, missing_ok=False)
                except encinfo.EncodingLookupError:
                    if encoding == 'CHARSET':
                        if not ctx.is_template:
                            self.tag('boilerplate-in-content-type', ct)
                    else:
                        self.tag('unknown-encoding', encoding)
                    encoding = None
                else:
                    if not is_ascii_compatible:
                        self.tag('non-ascii-compatible-encoding', encoding)
                    elif encinfo.is_portable_encoding(encoding):
                        pass
                    else:
                        new_encoding = encinfo.propose_portable_encoding(encoding)
                        if new_encoding is not None:
                            self.tag('non-portable-encoding', encoding, '=>', new_encoding)
                            encoding = new_encoding
                        else:
                            self.tag('non-portable-encoding', encoding)
                    if ctx.language is not None:
                        unrepresentable_characters = ctx.language.get_unrepresentable_characters(encoding)
                        if unrepresentable_characters:
                            if len(unrepresentable_characters) > 5:
                                unrepresentable_characters[4:] = ['...']
                            self.tag('unrepresentable-characters', encoding, *unrepresentable_characters)
                if match.group(1) is None:
                    if encoding is not None:
                        content_type_hint = content_type_hint.replace('<encoding>', encoding)
                    self.tag('invalid-content-type', ct, '=>', content_type_hint)
                if encoding is not None:
                    encodings.add(encoding)
            else:
                self.tag('invalid-content-type', ct, '=>', content_type_hint)
        if len(encodings) == 1:
            [ctx.encoding] = encodings

    @checks_header_fields('POT-Creation-Date', 'PO-Revision-Date')
    def check_dates(self, ctx):
        try:
            content_type = ctx.metadata['Content-Type'][0]
        except IndexError:
            content_type = ''
        is_publican = content_type.startswith('application/x-publican;')
        for field in 'POT-Creation-Date', 'PO-Revision-Date':
            dates = ctx.metadata[field]
            if len(dates) > 1:
                self.tag('duplicate-header-field-date', field)
                dates = sorted(set(dates))
            elif len(dates) == 0:
                if field.startswith('POT-') and ctx.is_binary:
                    # In gettext >> 0.19.8.1, msgfmt will be removing
                    # the POT-Creation-Date header.
                    # https://savannah.gnu.org/bugs/?49654
                    continue
                self.tag('no-date-header-field', field)
                continue
            for date in dates:
                if ctx.is_template and field.startswith('PO-') and (date == gettext.boilerplate_date):
                    continue
                if 'T' in date and is_publican:
                    # Publican uses DateTime->now(), which uses the UTC timezone by default:
                    # https://sources.debian.org/src/publican/2.8-3/lib/Publican/Translate.pm/?hl=748#L744
                    # https://bugs.debian.org/714739
                    tz_hint = '-0000'
                else:
                    tz_hint = None
                try:
                    fixed_date = gettext.fix_date_format(date, tz_hint=tz_hint)
                except gettext.BoilerplateDate:
                    self.tag('boilerplate-in-date', tags.safestr(field + ':'), date)
                    continue
                except gettext.DateSyntaxError:
                    self.tag('invalid-date', tags.safestr(field + ':'), date)
                    continue
                else:
                    if date != fixed_date:
                        self.tag('invalid-date', tags.safestr(field + ':'), date, '=>', fixed_date)
                stamp = gettext.parse_date(fixed_date)
                if stamp > misc.utc_now():
                    self.tag('date-from-future', tags.safestr(field + ':'), date)
                if stamp < gettext.epoch:
                    self.tag('ancient-date', tags.safestr(field + ':'), date)

    @checks_header_fields('Project-Id-Version', 'Report-Msgid-Bugs-To')
    def check_project(self, ctx):
        # Project-Id-Version:
        project_id_versions = ctx.metadata['Project-Id-Version']
        if len(project_id_versions) > 1:
            self.tag('duplicate-header-field-project-id-version')
            project_id_versions = sorted(set(project_id_versions))
        elif len(project_id_versions) == 0:
            self.tag('no-project-id-version-header-field')
        for project_id_version in project_id_versions:
            if project_id_version in {'PACKAGE VERSION', 'PROJECT VERSION'}:
                self.tag('boilerplate-in-project-id-version', project_id_version)
            else:
                if not re.search(r'[^_\d\W]', project_id_version):
                    self.tag('no-package-name-in-project-id-version', project_id_version)
                if not re.search(r'[0-9]', project_id_version):
                    self.tag('no-version-in-project-id-version', project_id_version)
        # Report-Msgid-Bugs-To:
        report_msgid_bugs_tos = ctx.metadata['Report-Msgid-Bugs-To']
        if len(report_msgid_bugs_tos) > 1:
            self.tag('duplicate-header-field-report-msgid-bugs-to')
            report_msgid_bugs_tos = sorted(set(report_msgid_bugs_tos))
        if report_msgid_bugs_tos == ['']:
            report_msgid_bugs_tos = []
        if len(report_msgid_bugs_tos) == 0:
            self.tag('no-report-msgid-bugs-to-header-field')
        for report_msgid_bugs_to in report_msgid_bugs_tos:
            real_name, email_address = email.utils.parseaddr(report_msgid_bugs_to)
            del real_name
            if '@' not in email_address:
                uri = urllib.parse.urlparse(report_msgid_bugs_to)
                if uri.scheme == '':
                    self.tag('invalid-report-msgid-bugs-to', report_msgid_bugs_to)
            elif domains.is_email_in_special_domain(email_address):
                self.tag('invalid-report-msgid-bugs-to', report_msgid_bugs_to)
            elif email_address == 'EMAIL@ADDRESS':
                self.tag('boilerplate-in-report-msgid-bugs-to', report_msgid_bugs_to)
            elif domains.is_email_in_dotless_domain(email_address):
                self.tag('invalid-report-msgid-bugs-to', report_msgid_bugs_to)

    @checks_header_fields('Last-Translator', 'Language-Team')
    def check_translator(self, ctx):
        # Last-Translator:
        translators = ctx.metadata['Last-Translator']
        if len(translators) > 1:
            self.tag('duplicate-header-field-last-translator')
            translators = sorted(set(translators))
        elif len(translators) == 0:
            self.tag('no-last-translator-header-field')
        translator_emails = {}
        for translator in translators:
            translator_name, translator_email = email.utils.parseaddr(translator)
            del translator_name
            translator_emails[translator_email] = translator
            if '@' not in translator_email:
                self.tag('invalid-last-translator', translator)
            elif domains.is_email_in_special_domain(translator_email):
                self.tag('invalid-last-translator', translator)
            elif translator_email == 'EMAIL@ADDRESS':
                if not ctx.is_template:
                    self.tag('boilerplate-in-last-translator', translator)
            elif domains.is_email_in_dotless_domain(translator_email):
                self.tag('invalid-last-translator', translator)
        # Language-Team:
        teams = ctx.metadata['Language-Team']
        if len(teams) > 1:
            self.tag('duplicate-header-field-language-team')
            teams = sorted(set(teams))
        elif len(teams) == 0:
            self.tag('no-language-team-header-field')
        for team in teams:
            team_name, team_email = email.utils.parseaddr(team)
            del team_name
            if '@' not in team_email:
                # TODO: A URL is also allowed here.
                # self.tag('invalid-language-team', translator)
                pass
            elif domains.is_email_in_special_domain(team_email):
                self.tag('invalid-language-team', team)
            elif team_email in {'LL@li.org', 'EMAIL@ADDRESS'}:
                if not ctx.is_template:
                    self.tag('boilerplate-in-language-team', team)
            elif domains.is_email_in_dotless_domain(team_email):
                self.tag('invalid-language-team', team)
            else:
                translator = translator_emails.get(team_email)
                if translator is not None:
                    self.tag('language-team-equal-to-last-translator', team, translator)

    def check_headers(self, ctx):
        metadata = collections.defaultdict(list)
        strays = []
        ctx.file.header_entry = None
        seen_header_entry = False
        for entry in ctx.file:
            if not is_header_entry(entry) or entry.obsolete:
                continue
            if seen_header_entry:
                self.tag('duplicate-header-entry')
                break
            if entry.occurrences:
                self.tag('empty-msgid-message-with-source-code-references',
                    *(':'.join((path, line)) for path, line in entry.occurrences)
                )
            if entry.msgid_plural is not None:
                self.tag('empty-msgid-message-with-plural-forms')
            msgstr = entry.msgstr_plural.get(0, entry.msgstr)
            # At least in polib 1.0.0, if the source PO file is empty,
            # msgstr for the header entry is None.
            msgstr = msgstr or ''
            for line in gettext.parse_header(msgstr):
                if isinstance(line, dict):
                    [(key, value)] = line.items()
                    metadata[key] += [value]
                else:
                    strays += [line]
            flags = collections.Counter(entry.flags)
            for flag, n in sorted(flags.items()):
                if flag == 'fuzzy':
                    if not ctx.is_template:
                        self.tag('fuzzy-header-entry')
                elif difflib.get_close_matches(flag.lower(), ['fuzzy'], cutoff=0.8):
                    self.tag('unexpected-flag-for-header-entry', flag, '=>', 'fuzzy')
                else:
                    self.tag('unexpected-flag-for-header-entry', flag)
                if n > 1:
                    self.tag('duplicate-flag-for-header-entry', flag)
            if entry is not ctx.file[0]:
                self.tag('distant-header-entry')
            unusual_chars = set(find_unusual_characters(msgstr))
            if unusual_chars:
                unusual_char_names = ', '.join(
                    'U+{:04X} {}'.format(ord(ch), encinfo.get_character_name(ch))
                    for ch in sorted(unusual_chars)
                )
                self.tag('unusual-character-in-header-entry', tags.safestr(unusual_char_names))
            seen_header_entry = True
        seen_conflict_marker = False
        for stray in strays:
            if gettext.search_for_conflict_marker(stray):
                if not seen_conflict_marker:
                    self.tag('conflict-marker-in-header-entry', stray)
                    seen_conflict_marker = True
            else:
                self.tag('stray-header-line', stray)
        header_fields = frozenset(gettext.header_fields)
        header_fields_lc = {str.lower(s): s for s in header_fields}
        for key, values in sorted(metadata.items()):
            if key.startswith(('X-', 'x-')):
                pass  # ok
            elif key in header_fields:
                pass  # ok
            else:
                hint = header_fields_lc.get(key.lower())
                if hint is None:
                    hints = difflib.get_close_matches(key, header_fields, n=1, cutoff=0.8)
                    if hints:
                        [hint] = hints
                if hint in metadata:
                    hint = None
                if hint is None:
                    self.tag('unknown-header-field', key)
                else:
                    self.tag('unknown-header-field', key, '=>', hint)
            if len(values) > 1 and key not in header_fields_with_dedicated_checks:
                self.tag('duplicate-header-field', key)
        ctx.metadata = metadata
        del ctx.file.metadata
        del ctx.file.metadata_is_fuzzy

    def check_messages(self, ctx):
        found_unusual_characters = set()
        msgid_counter = collections.Counter()
        for message in ctx.file:
            if message.obsolete:
                continue
            if is_header_entry(message):
                continue
            flags = self._check_message_flags(message)
            self._check_message_formats(ctx, message, flags)
            msgid_counter[message.msgid, message.msgctxt] += 1
            if msgid_counter[message.msgid, message.msgctxt] == 2:
                self.tag('duplicate-message-definition', message_repr(message))
            has_msgstr = bool(message.msgstr)
            has_msgstr_plural = any(message.msgstr_plural.values())
            if ctx.is_template:
                if has_msgstr or has_msgstr_plural:
                    self.tag('translation-in-template', message_repr(message))
            leading_lf = message.msgid.startswith('\n')
            trailing_lf = message.msgid.endswith('\n')
            has_previous_msgid = any(s is not None for s in [
                message.previous_msgctxt,
                message.previous_msgid,
                message.previous_msgid_plural,
            ])
            if has_previous_msgid and not flags.fuzzy:
                self.tag('stray-previous-msgid', message_repr(message))
            strings = []
            if message.msgid_plural is not None:
                strings += [message.msgid_plural]
            if not flags.fuzzy:
                if has_msgstr:
                    strings += [message.msgstr]
                if has_msgstr_plural:
                    strings += message.msgstr_plural.values()  # the order doesn't matter here
            for s in strings:
                if s.startswith('\n') != leading_lf:
                    self.tag('inconsistent-leading-newlines', message_repr(message))
                    break
            for s in strings:
                if s.endswith('\n') != trailing_lf:
                    self.tag('inconsistent-trailing-newlines', message_repr(message))
                    break
            strings = []
            if has_msgstr:
                strings += [message.msgstr]
            if has_msgstr_plural:
                strings += misc.sorted_vk(message.msgstr_plural)
            if ctx.encoding is not None:
                msgid_uc = (
                    set(find_unusual_characters(message.msgid)) |
                    set(find_unusual_characters(message.msgid_plural or ''))
                )
                for msgstr in strings:
                    msgstr_uc = set(find_unusual_characters(msgstr))
                    uc = msgstr_uc - msgid_uc - found_unusual_characters
                    if not uc:
                        continue
                    names = ', '.join(
                        'U+{:04X} {}'.format(ord(ch), encinfo.get_character_name(ch))
                        for ch in sorted(uc)
                    )
                    self.tag('unusual-character-in-translation',
                        message_repr(message, template='{}:'),
                        tags.safestr(names)
                    )
                    found_unusual_characters |= uc
            if not flags.fuzzy:
                for msgstr in strings:
                    conflict_marker = gettext.search_for_conflict_marker(msgstr)
                    if conflict_marker is not None:
                        conflict_marker = conflict_marker.group(0)
                        self.tag('conflict-marker-in-translation', message_repr(message), conflict_marker)
                        break
                if has_msgstr_plural and not all(message.msgstr_plural.values()):
                    self.tag('partially-translated-message', message_repr(message))
        if len(msgid_counter) == 0:
            possible_hidden_strings = False
            if ctx.is_binary:
                possible_hidden_strings = ctx.file.possible_hidden_strings
            if not possible_hidden_strings:
                self.tag('empty-file')

    def _check_message_flags(self, message):
        info = types.SimpleNamespace()
        info.fuzzy = False
        info.range_min = 0
        info.range_max = 1e999  # +inf
        info.formats = None
        flags = collections.Counter(message.flags)
        wrap = None
        format_flags = collections.defaultdict(dict)
        range_flags = collections.defaultdict(
            collections.Counter
        )
        for flag, n in sorted(flags.items()):
            known_flag = True
            if flag == 'fuzzy':
                info.fuzzy = True
            elif flag in {'wrap', 'no-wrap'}:
                new_wrap = flag == 'wrap'
                if wrap == (not new_wrap):
                    self.tag('conflicting-message-flags',
                        message_repr(message, template='{}:'),
                        'wrap', 'no-wrap'
                    )
                else:
                    wrap = new_wrap
            elif flag.startswith('range:'):
                if message.msgid_plural is None:
                    self.tag('range-flag-without-plural-string')
                match = re.match(r'\A([0-9]+)[.][.]([0-9]+)\Z', flag[6:].strip(' \t\r\f\v'))
                if match is not None:
                    i, j = map(int, match.groups())
                    if i < j:
                        info.range_min = i
                        info.range_max = j
                    else:
                        match = None
                if match is None:
                    self.tag('invalid-range-flag',
                        message_repr(message, template='{}:'),
                        flag
                    )
                else:
                    range_flags[i, j][flag] += n
                    n = 0
            elif flag.endswith('-format'):
                known_flag = False
                for prefix in 'no-', 'possible-', 'impossible-', '':
                    tp = prefix.rstrip('-')
                    if not flag.startswith(prefix):
                        continue
                    string_format = flag[len(prefix):-7]
                    if string_format in gettext.string_formats:
                        known_flag = True
                        format_flags[tp][string_format] = flag
                        break
            else:
                known_flag = False
            if not known_flag:
                self.tag('unknown-message-flag',
                    message_repr(message, template='{}:'),
                    flag
                )
            if n > 1 and flag:
                self.tag('duplicate-message-flag',
                    message_repr(message, template='{}:'),
                    flag
                )
        if len(range_flags) > 1:
            [range1, range2] = heapq.nsmallest(2, range_flags.keys())
            self.tag('conflicting-message-flags',
                message_repr(message, template='{}:'),
                min(range_flags[range1].keys()),
                min(range_flags[range2].keys()),
            )
        elif len(range_flags) == 1:
            [range_flags] = range_flags.values()
            if sum(range_flags.values()) > 1:
                self.tag('duplicate-message-flag',
                    message_repr(message, template='{}:'),
                    min(range_flags.keys())
                )
        positive_format_flags = format_flags['']
        info.formats = frozenset(positive_format_flags)
        for fmt1, flag1 in sorted(positive_format_flags.items()):
            for fmt2, flag2 in sorted(positive_format_flags.items()):
                if fmt1 >= fmt2:
                    continue
                fmt_ex1 = gettext.string_formats[fmt1]
                fmt_ex2 = gettext.string_formats[fmt2]
                if fmt_ex1 & fmt_ex2:
                    # the formats are, at least to some extent, compatible
                    continue
                self.tag('conflicting-message-flags',
                    message_repr(message, template='{}:'),
                    flag1, flag2
                )
        for positive_key, negative_key in [('', 'no'), ('', 'impossible'), ('possible', 'impossible')]:
            positive_format_flags = format_flags[positive_key]
            negative_format_flags = format_flags[negative_key]
            conflicting_formats = frozenset(positive_format_flags) & frozenset(negative_format_flags)
            for fmt in sorted(conflicting_formats):
                self.tag('conflicting-message-flags',
                    message_repr(message, template='{}:'),
                    positive_format_flags[fmt],
                    negative_format_flags[fmt],
                )
        positive_format_flags = format_flags['']
        possible_format_flags = format_flags['possible']
        redundant_formats = frozenset(positive_format_flags) & frozenset(possible_format_flags)
        for fmt in sorted(redundant_formats):
            self.tag('redundant-message-flag',
                message_repr(message, template='{}:'),
                possible_format_flags[fmt],
                tags.safe_format('(implied by {flag})'.format(flag=positive_format_flags[fmt]))
            )
        return info

    def _check_message_formats(self, ctx, message, flags):
        for fmt in sorted(flags.formats):
            try:
                checker = self._message_format_checkers[fmt]
            except KeyError:
                continue
            checker.check_message(ctx, message, flags)
        if re.match(r'\Atype: Content of: (<{xmlname}>)+\Z'.format(xmlname=xml.name_re), message.comment or ''):
            self._check_message_xml_format(ctx, message, flags)

    def _check_message_xml_format(self, ctx, message, flags):
        if ctx.encoding is None:
            return
        prefix = message_repr(message, template='{}:')
        try:
            xml.check_fragment(message.msgid)
        except xml.SyntaxError as exc:
            if ctx.is_template:
                self.tag('malformed-xml', prefix, tags.safestr(exc))
            return
        if flags.fuzzy:
            return
        if not message.msgstr:
            return
        try:
            xml.check_fragment(message.msgstr)
        except xml.SyntaxError as exc:
            self.tag('malformed-xml', prefix, tags.safestr(exc))

__all__ = ['Checker']

def is_header_entry(entry):
    return (
        entry.msgid == '' and
        entry.msgctxt is None
    )

# vim:ts=4 sts=4 sw=4 et
