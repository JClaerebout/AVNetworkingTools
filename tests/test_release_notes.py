from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
import release_notes
from http_test_support import authorized_client


class ReleaseNotesTests(unittest.TestCase):
    def test_seen_version_survives_app_restart_and_next_version_shows(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            release_notes, 'STATE_FILE', Path(directory) / 'seen.json'
        ):
            client = authorized_client(app.create_app())
            notes = client.get('/api/release-notes').json['notes']
            self.assertEqual(notes['version'], release_notes.APP_VERSION)
            self.assertTrue(notes['highlights'])
            # Reading alone must not hide notes before the UI displays them.
            self.assertIsNotNone(client.get('/api/release-notes').json['notes'])
            self.assertEqual(client.post('/api/release-notes/seen').status_code, 200)
            restarted = authorized_client(app.create_app())
            self.assertIsNone(restarted.get('/api/release-notes').json['notes'])
            with patch.object(release_notes, 'APP_VERSION', '99.0.0.1'):
                self.assertIsNotNone(restarted.get('/api/release-notes').json['notes'])

    def test_acknowledgement_requires_action_token(self):
        self.assertEqual(app.create_app().test_client().post('/api/release-notes/seen').status_code, 403)


if __name__ == '__main__':
    unittest.main()
