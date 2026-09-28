from http_test_support import authorized_client
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import app
from export_utils import SaveCancelled, choose_save_path, save_export


class SaveDialogTests(unittest.TestCase):
    def test_cancel_does_not_write(self):
        window = Mock()
        window.create_file_dialog.return_value = None
        with patch('export_utils.webview.windows', [window]), patch.object(Path, 'write_bytes') as write:
            with self.assertRaises(SaveCancelled):
                save_export('report.txt', 'content')
            write.assert_not_called()

    def test_uses_selected_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / 'my-custom-name.txt'
            window = Mock()
            window.create_file_dialog.return_value = (str(selected),)
            with patch('export_utils.webview.windows', [window]):
                self.assertEqual(save_export('report.txt', 'hello'), selected.resolve())
            self.assertEqual(selected.read_text(), 'hello')

    @patch('routes.get_ping_status', return_value={'output': ['reply']})
    @patch('export_utils.choose_save_path', side_effect=SaveCancelled)
    def test_cancel_response(self, choose, status):
        response = authorized_client(app).post('/ping/export.txt')
        self.assertTrue(response.get_json()['cancelled'])
        self.assertFalse(response.get_json()['success'])

    @patch('export_utils.webview.windows', [])
    def test_missing_desktop_window(self):
        with self.assertRaises(OSError):
            choose_save_path('report.txt')
