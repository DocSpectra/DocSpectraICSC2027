"""Release-boundary checks: path escape, changed inputs and archive contents."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import submission_artifacts_42b as release


class SubmissionBoundaryTests(unittest.TestCase):
    def test_portable_path_boundary(self):
        for value in ('../secret', '/absolute', 'C:/private', 'a\\b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.safe_path(release.ROOT, value)

    def test_changed_input_refuses_before_output(self):
        with tempfile.TemporaryDirectory(dir=release.ROOT/'.tmp') as td:
            root=Path(td);source=root/'input.txt';source.write_bytes(b'changed')
            plan=root/'plan.json'
            plan.write_text(json.dumps({'files':[{'source':source.relative_to(release.ROOT).as_posix(),
                'path':'input.txt','sha256':hashlib.sha256(b'original').hexdigest()}]}))
            with self.assertRaises(AssertionError):release.build(plan,root/'candidate')
            self.assertFalse((root/'candidate').exists())

    def test_archive_matches_explicit_inputs(self):
        with tempfile.TemporaryDirectory(dir=release.ROOT/'.tmp') as td:
            root=Path(td);source=root/'input.bin';source.write_bytes(bytes(range(256)))
            archive=root/'records.zip';release.pack_zip(archive,{'records/input.bin':source})
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(z.namelist(),['records/input.bin'])
                self.assertEqual(z.read('records/input.bin'),source.read_bytes())


if __name__=='__main__':unittest.main()
