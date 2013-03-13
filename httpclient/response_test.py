"""Tests for protocol.py"""

import unittest
import unittest.mock

import tulip

from . import response
from . import protocol


class ResponseTests(unittest.TestCase):

    def setUp(self):
        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

        self.transport = unittest.mock.Mock()
        self.stream = protocol.HttpStreamReader(self.transport)
        self.response = response.HttpResponse('get', 'http://python.org')

    def tearDown(self):
        self.loop.close()

    def test_close(self):
        self.response.stream = self.stream
        self.response.close()
        self.assertIsNone(self.response.stream)
        self.assertTrue(self.transport.close.called)

    def test_isclosed(self):
        self.response.stream = self.stream
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
            self.loop.run_until_complete,
            tulip.Task(self.response.start(self.stream)))
