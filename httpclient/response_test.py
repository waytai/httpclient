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

    def test_broken_length(self):
        self.stream.feed_data(
            b'HTTP/1.1 200 Ok\r\n'
            b'Content-Length: ert\r\n\r\n')

        self.assertRaises(
            ValueError, self.loop.run_until_complete,
            tulip.Task(self.response.start(self.stream)))

    def _test_head_length_zero(self):
        self.response.method = 'HEAD'
        self.stream.feed_data(
            b'HTTP/1.1 200 Ok\r\n'
            b'Content-Length: 4\r\n\r\ntest')

        r = self.loop.run_until_complete(
            tulip.Task(self.response.start(self.stream, True)))
        self.assertEqual(b'', r.content)

    def _test_length_below_zero(self):
        self.stream.feed_data(
            b'HTTP/1.1 200 Ok\r\n'
            b'Content-Length: -1\r\n\r\ntest')
        self.stream.feed_eof()

        r = self.loop.run_until_complete(
            tulip.Task(self.response.start(self.stream, True)))
        self.assertEqual(b'test', r.content)
