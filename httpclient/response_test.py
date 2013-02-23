"""Tests for protocol.py"""

import http.client
import unittest
import unittest.mock
import urllib.parse

import tulip

from . import response
from . import protocol


class ResponseTests(unittest.TestCase):

    def setUp(self):
        self.ev = tulip.new_event_loop()
        tulip.set_event_loop(self.ev)

        self.transport = unittest.mock.Mock()
        self.stream = protocol.HttpStreamReader(self.transport)
        self.response = response.HttpResponse('get', 'http://python.org')
        self.response.stream = self.stream

    def tearDown(self):
        self.ev.close()

    def test_close(self):
        self.response.close()
        self.assertIsNone(self.response.stream)
        self.assertTrue(self.transport.close.called)

    def test_isclosed(self):
        self.assertFalse(self.response.isclosed())
        self.response.close()
        self.assertTrue(self.response.isclosed())

    def test_repr(self):
        self.response.status = 200
        self.response.reason = 'Ok'
        self.assertIn('<HttpResponse [200 Ok]>', repr(self.response))

    def test_start_start(self):
        self.response.stream = self.stream

        self.assertRaises(
            RuntimeError,
            self.ev.run_until_complete,
            tulip.Task(self.response.start(self.stream)))
