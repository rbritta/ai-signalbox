"""The release notes (scripts/release_notes.py) and the version: the same in every place it lives, with its section in
the CHANGELOG under an Unreleased one."""
import importlib.util
import re
import sys
import unittest

from helpers import ROOT

from ai_signalbox import __version__

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-01-02

### Fixed
- The thing.

## [1.1.0] - 2026-01-01

### Added
- The other thing.

## [1.0.0] - 2025-12-31

[Unreleased]: https://example.com/compare/v1.2.0...HEAD
[1.2.0]: https://example.com/compare/v1.1.0...v1.2.0
"""


def load_script():
    spec = importlib.util.spec_from_file_location('signalbox_release_notes', ROOT / 'scripts' / 'release_notes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rn = load_script()


class ReleaseNotesTest(unittest.TestCase):
    def test_a_section_ends_at_the_next_heading(self):
        self.assertEqual('### Fixed\n- The thing.', rn.changelog_section(CHANGELOG, '1.2.0'))
        self.assertEqual('### Added\n- The other thing.', rn.changelog_section(CHANGELOG, '1.1.0'))

    def test_the_last_section_ends_at_the_link_references(self):
        self.assertIsNone(rn.changelog_section(CHANGELOG, '1.0.0'))  # empty

    def test_a_missing_version_or_a_prefix_of_one_has_no_section(self):
        self.assertIsNone(rn.changelog_section(CHANGELOG, '9.9.9'))
        self.assertIsNone(rn.changelog_section(CHANGELOG, '1.2'))

    def test_windows_line_endings_read_the_same(self):
        self.assertEqual('### Fixed\n- The thing.', rn.changelog_section(CHANGELOG.replace('\n', '\r\n'), '1.2.0'))

    def test_the_tag_must_name_the_version(self):
        self.assertEqual('### Fixed\n- The thing.', rn.release_notes('v1.2.0', CHANGELOG, '1.2.0'))
        self.assertEqual('### Fixed\n- The thing.', rn.release_notes('refs/tags/v1.2.0', CHANGELOG, '1.2.0'))
        with self.assertRaisesRegex(ValueError, 'does not name the version'):
            rn.release_notes('v1.1.0', CHANGELOG, '1.2.0')
        with self.assertRaisesRegex(ValueError, r'\(none\)'):
            rn.release_notes('', CHANGELOG, '1.2.0')

    def test_an_empty_section_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'no section, or an empty one'):
            rn.release_notes('v1.0.0', CHANGELOG, '1.0.0')


class VersionTest(unittest.TestCase):
    def test_pyproject_and_the_package_agree(self):
        project, package = rn.versions()
        self.assertEqual(project, package)
        self.assertEqual(__version__, package)

    def test_the_changelog_has_unreleased_on_top_and_a_section_for_this_version(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        headings = re.findall(r'^## \[([^\]]+)\]', text, re.M)
        self.assertEqual('Unreleased', headings[0])
        self.assertEqual(__version__, headings[1])
        self.assertTrue(rn.changelog_section(text, __version__))
        self.assertRegex(text, rf'(?m)^## \[{re.escape(__version__)}\] - \d{{4}}-\d\d-\d\d$')

    def test_requires_python_leaves_out_the_argparse_that_refuses_words_after_an_option(self):
        import operator
        import tomllib
        with open(ROOT / 'pyproject.toml', 'rb') as fh:
            spec = tomllib.load(fh)['project']['requires-python']
        ops = {'>=': operator.ge, '<=': operator.le, '!=': operator.ne, '==': operator.eq, '>': operator.gt,
               '<': operator.lt}

        def allowed(version):
            v = tuple(int(x) for x in version.split('.'))
            for clause in spec.split(','):
                m = re.fullmatch(r'\s*(>=|<=|!=|==|>|<)\s*([\d.]+)\s*', clause)
                self.assertIsNotNone(m, f'unexpected clause {clause!r} in requires-python')
                bound = tuple(int(x) for x in m.group(2).split('.'))
                if not ops[m.group(1)](v, bound + (0,) * (len(v) - len(bound))):
                    return False
            return True
        for refused in ('3.11.9', '3.12.0', '3.12.6', '3.13.0'):
            self.assertFalse(allowed(refused), refused)
        for fine in ('3.12.7', '3.12.12', '3.13.1', '3.14.0'):
            self.assertTrue(allowed(fine), fine)

    def test_words_after_an_option_reach_the_positional_on_this_python(self):
        from ai_signalbox import cli
        args = cli.build_parser().parse_args(['hold', 'db', '--shared', 'read', 'the', 'schema'])
        self.assertEqual((True, ['read', 'the', 'schema']), (args.shared, args.why))

    def test_the_script_prints_the_notes_of_this_version(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, rn.main([f'v{__version__}']))
        self.assertTrue(out.getvalue().strip())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(1, rn.main(['v0.0.0']))
        self.assertIn('does not name the version', err.getvalue())


if __name__ == '__main__':
    unittest.main()
