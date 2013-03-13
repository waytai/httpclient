"""Tests for protocol.py"""

import unittest
import unittest.mock

import tulip
import tulip.http

from . import utils
from . import protocol


class HttpStreamReaderTests(unittest.TestCase):

    def setUp(self):
        self.ev = tulip.new_event_loop()
        tulip.set_event_loop(self.ev)

        self.transport = unittest.mock.Mock()
        self.stream = protocol.HttpStreamReader(self.transport)

    def test_ctor(self):
        self.ev.close()

        self.assertIs(self.stream.transport, self.transport)

    def test_close(self):
        self.stream.close()
        self.assertTrue(self.transport.close.called)


class HttpStreamWriterTests(unittest.TestCase):

    def setUp(self):
        self.transport = unittest.mock.Mock()
        self.writer = protocol.HttpStreamWriter(self.transport)

    def test_ctor(self):
        transport = unittest.mock.Mock()
        writer = protocol.HttpStreamWriter(transport, 'latin-1')
        self.assertIs(writer.transport, transport)
        self.assertEqual(writer.encoding, 'latin-1')

    def test_encode(self):
        self.assertEqual(b'test', self.writer.encode('test'))
        self.assertEqual(b'test', self.writer.encode(b'test'))

    def test_decode(self):
        self.assertEqual('test', self.writer.decode('test'))
        self.assertEqual('test', self.writer.decode(b'test'))

    def test_write(self):
        self.writer.write(b'test')
        self.assertTrue(self.transport.write.called)
        self.assertEqual((b'test',), self.transport.write.call_args[0])

    def test_write_str(self):
        self.writer.write_str('test')
        self.assertTrue(self.transport.write.called)
        self.assertEqual((b'test',), self.transport.write.call_args[0])

    def test_write_cunked(self):
        self.writer.write_chunked('')
        self.assertFalse(self.transport.write.called)

        self.writer.write_chunked('data')
        self.assertEqual(
            [(b'4\r\n',), (b'data',), (b'\r\n',)],
            [c[0] for c in self.transport.write.call_args_list])

    def test_write_eof(self):
        self.writer.write_chunked_eof()
        self.assertEqual((b'0\r\n\r\n',), self.transport.write.call_args[0])

    def test_write_payload_simple(self):
        write = self.writer.write = unittest.mock.Mock()

        self.writer.write_payload(b'data')
        self.assertTrue((b'data',), write.call_args[0])

        write.reset_mock()
        self.writer.write_payload((b'data1', 'data2'))
        self.assertTrue(2, write.call_count)

    def test_write_payload_simple_chunked(self):
        write = self.writer.write_chunked = unittest.mock.Mock()

        self.writer.write_payload(b'data', chunked=True)
        self.assertTrue((b'data',), write.call_args[0])

        write.reset_mock()
        self.writer.write_payload((b'data1', 'data2'), chunked=True)
        self.assertTrue(2, write.call_count)

    def test_write_payload_writers(self):
        write = self.writer.write = unittest.mock.Mock()

        self.writer.write_payload(b'data', [utils.ChunkedIter(2)])
        self.assertTrue((b'da',), write.call_args[0])
        self.assertTrue(2, write.call_count)

        write.reset_mock()
        self.writer.write_payload(
            (b'data1', b'data2'), [utils.ChunkedIter(2)])
        self.assertTrue(5, write.call_count)

    def test_write_payload_writers_chunked(self):
        write = self.writer.write_chunked = unittest.mock.Mock()

        self.writer.write_payload(
            b'data', [utils.ChunkedIter(2)], True)
        self.assertTrue((b'da',), write.call_args[0])
        self.assertTrue(2, write.call_count)

        write.reset_mock()
        self.writer.write_payload(
            (b'data1', b'data2',),
            [utils.ChunkedIter(2), utils.DeflateIter()], True)
        self.assertTrue(5, write.call_count)


class HttpProtocolTests(unittest.TestCase):

    def test_protocol(self):
        transport = unittest.mock.Mock()

        p = protocol.HttpProtocol()
        p.connection_made(transport)
        self.assertIs(p.transport, transport)
        self.assertIsInstance(p.rstream, protocol.HttpStreamReader)
        self.assertIsInstance(p.wstream, protocol.HttpStreamWriter)

        p.data_received(b'data')
        self.assertEqual(4, p.rstream.byte_count)

        p.eof_received()
        self.assertTrue(p.rstream.eof)

        p.connection_lost(None)
