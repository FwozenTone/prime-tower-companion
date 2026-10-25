import threading
import unittest
from unittest.mock import patch, MagicMock
import subprocess
import tower_wizard as wizard

class StartupTests(unittest.TestCase):
    def test_slow_browser_does_not_block_server(self):
        entered=threading.Event(); serving=threading.Event(); finished=threading.Event()
        def browser(url):
            entered.set()
            try:
                if not serving.wait(2): raise AssertionError('Server blocked by browser')
            finally: finished.set()
        server=MagicMock(server_port=12345)
        def serve():
            self.assertTrue(entered.wait(2))
            self.assertFalse(finished.is_set())
            serving.set()
            self.assertTrue(finished.wait(2))
            raise KeyboardInterrupt
        server.serve_forever.side_effect=serve
        with patch.object(wizard,'Workflow'),patch.object(wizard,'ThreadingHTTPServer',return_value=server),patch.object(wizard,'open_wizard_browser',side_effect=browser):
            wizard.serve()
        server.server_close.assert_called_once()

    def test_macos_browser_open_is_bounded(self):
        with patch.object(wizard.sys,'platform','darwin'),patch.object(wizard.subprocess,'run') as run:
            wizard.open_wizard_browser('http://127.0.0.1:12345/')
        run.assert_called_once_with(['/usr/bin/open','http://127.0.0.1:12345/'],check=True,timeout=15)

    def test_browser_timeout_reports_manual_url(self):
        with patch.object(wizard.sys,'platform','darwin'),patch.object(wizard.subprocess,'run',side_effect=subprocess.TimeoutExpired('open',15)),patch('builtins.print') as output:
            wizard.open_wizard_browser('http://127.0.0.1:12345/')
        self.assertIn('http://127.0.0.1:12345/',output.call_args.args[0])

    def test_no_browser_mode(self):
        server=MagicMock(server_port=12345);server.serve_forever.side_effect=KeyboardInterrupt
        with patch.object(wizard,'Workflow'),patch.object(wizard,'ThreadingHTTPServer',return_value=server),patch.object(wizard,'open_wizard_browser') as browser:
            wizard.serve(False)
        browser.assert_not_called()
        server.server_close.assert_called_once()
