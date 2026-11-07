import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SetupTests(unittest.TestCase):
    def app(self):
        path = ROOT / 'setup_local.py'
        self.assertTrue(path.exists(), 'setup_local.py must implement private local configuration')
        spec = importlib.util.spec_from_file_location('setup_local', path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_init_generates_secrets_and_does_not_overwrite(self):
        module = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.initialize(root)
            first = (root / '.env').read_text()
            module.initialize(root)
            self.assertEqual((root / '.env').read_text(), first)
            self.assertEqual((root / '.env').stat().st_mode & 0o777, 0o600)
            self.assertTrue((root / 'private/config.json').exists())
            self.assertNotIn('changeme', first)

    def test_build_sources_are_exact_and_not_private(self):
        module = self.app()
        self.assertEqual(module.VEXA_COMMIT, 'f1a62fbbdfd2c98cb162582d82c31fe8102acc89')
        self.assertEqual(module.VEXA_REPO, 'https://github.com/Vexa-ai/vexa.git')

if __name__ == '__main__': unittest.main()
