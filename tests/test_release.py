"""Safe offline smoke checks for release packaging."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class ReleaseTests(unittest.TestCase):
    def test_ui_language_selector_and_protocol(self):
        html = (ROOT/'autocontinue/ui/control.html').read_text(encoding='utf-8')
        for term in ['id="language"', 'localStorage', 'translations', '【全部完成】', '【需要人类】', 'MutationObserver']:
            self.assertIn(term, html)

    def test_manuals_and_license(self):
        for name in ['README.md','README.en.md','QUICKSTART.md','LICENSE','.gitignore']:
            self.assertTrue((ROOT/name).is_file(), name)
        self.assertIn('English', (ROOT/'README.en.md').read_text(encoding='utf-8'))

    def test_private_files_ignored(self):
        ignore=(ROOT/'.gitignore').read_text()
        for name in ['output/','chrome-profile/','runtime/','mcp.token']:
            self.assertIn(name,ignore)

    def test_csrf_protection_exists(self):
        code=(ROOT/'autocontinue/api.py').read_text(encoding='utf-8')
        self.assertIn('Cross-origin writes are blocked',code)

    def test_real_world_screenshots_documented(self):
        screenshots = ROOT / 'docs' / 'screenshots'
        self.assertTrue((screenshots / 'real-run-session-dashboard.png').is_file())
        self.assertTrue((screenshots / 'real-run-event-log.jpg').is_file())
        self.assertIn('real-run-session-dashboard.png', (ROOT / 'README.md').read_text(encoding='utf-8'))
        self.assertIn('real-run-event-log.jpg', (ROOT / 'README.en.md').read_text(encoding='utf-8'))

if __name__ == '__main__': unittest.main()
