"""Tests for protocol.py"""

import http.client
import unittest
import unittest.mock
import urllib.parse
import zlib
import gzip

import tulip

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

    def test_response_status_bad_status_line(self):
        self.stream.feed_data(b'\r\n')
        self.assertRaises(
            http.client.BadStatusLine,
            self.ev.run_until_complete,
            tulip.Task(self.stream.read_response_status()))

        self.stream.feed_eof()
        self.assertRaises(
            http.client.BadStatusLine,
            self.ev.run_until_complete,
            tulip.Task(self.stream.read_response_status()))

    def test_response_status_no_reason(self):
        self.stream.feed_data(b'HTTP/1.1 200\r\n')

        v, s, r = self.ev.run_until_complete(
            tulip.Task(self.stream.read_response_status()))
        self.assertEqual(v, (1, 1))
        self.assertEqual(s, 200)
        self.assertEqual(r, '')

    def test_response_status_bad(self):
        self.stream.feed_data(b'HTT/1\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.ev.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTT/1', str(cm.exception))

    def test_response_status_bad_code(self):
        self.stream.feed_data(b'HTTP/1.1 99 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.ev.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 99 test', str(cm.exception))

        self.stream.feed_data(b'HTTP/1.1 9999 test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.ev.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 9999 test', str(cm.exception))

        self.stream.feed_data(b'HTTP/1.1 ttt test\r\n')
        with self.assertRaises(http.client.BadStatusLine) as cm:
            self.ev.run_until_complete(
                tulip.Task(self.stream.read_response_status()))

        self.assertIn('HTTP/1.1 ttt test', str(cm.exception))

    def test_parse_headers_invalid_header(self):
        with self.assertRaises(ValueError) as cm:
            self.stream._parse_headers(['test line\r\n'])

        self.assertIn("Invalid header 'test line'", str(cm.exception))

    def test_parse_headers_invalid_name(self):
        with self.assertRaises(ValueError) as cm:
            self.stream._parse_headers(['test[]: line\r\n'])

        self.assertIn("Invalid header name 'TEST[]'", str(cm.exception))

    def test_parse_headers_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.stream._parse_headers(['test: line data data\r\n'])

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_parse_headers_continuation_headers_size(self):
        self.stream.MAX_HEADERFIELD_SIZE = 5

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.stream._parse_headers(['test: line\r\n', ' test'])

        self.assertIn("limit request headers fields size", str(cm.exception))

    def test_parse_headers_max_size(self):
        self.stream.MAX_HEADERS = 5

        with self.assertRaises(http.client.LineTooLong) as cm:
            self.stream._parse_headers(
                ['test: line\r\n', 'test2: data\r\n'])

        self.assertIn("limit request headers fields", str(cm.exception))

    def test_parse_headers(self):
        headers = self.stream._parse_headers(
            ['test: line\r\n', 'test2: data\r\n'])
        self.assertEqual([('TEST', 'line'), ('TEST2', 'data')], headers)

    def test_parse_headers_continuation(self):
        headers = self.stream._parse_headers(['test: line\r\n', ' test'])
        self.assertEqual([('TEST', 'line\r\n test')], headers)


class StreamWriterTests(unittest.TestCase):

    def setUp(self):
        self.transport = unittest.mock.Mock()
        self.writer = protocol.StreamWriter(self.transport)

    def test_ctor(self):
        transport = unittest.mock.Mock()
        writer = protocol.StreamWriter(transport, 'latin-1')
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


class ChunkedStreamReaderTests(unittest.TestCase):

    def setUp(self):
        self.stream = protocol.HttpStreamReader()
        self.reader = protocol.ChunkedStreamReader(self.stream)
        self.event_loop = tulip.new_event_loop()
        tulip.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_next_chunk_size(self):
        self.stream.feed_data(b'4;test\r\n')
        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader._read_next_chunk_size()))
        self.assertEqual(4, data)

        self.stream.feed_data(b'4\r\n')
        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader._read_next_chunk_size()))
        self.assertEqual(4, data)

    def test_next_chunk_size_error(self):
        self.stream.feed_data(b'blah\r\n')
        self.assertRaises(
            ValueError,
            self.event_loop.run_until_complete,
            tulip.Task(self.reader._read_next_chunk_size()))

    def test_read_size_error(self):
        self.stream.feed_data(b'blah\r\n')
        self.assertRaises(
            http.client.IncompleteRead,
            self.event_loop.run_until_complete,
            tulip.Task(self.reader.read()))

    def test_read(self):
        self.stream.feed_data(b'4\r\ndata\r\n4\r\nline\r\n0\r\n\r\n')

        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader.read()))
        self.assertEqual(b'data', data)

        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader.read()))
        self.assertEqual(b'line', data)

        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader.read()))
        self.assertEqual(b'', data)

        self.assertTrue(self.reader.finished)

    def test_read_finished(self):
        self.stream.feed_data(b'4\r\ndata\r\n4\r\nline\r\n0\r\n\r\n')
        self.reader.finished = True

        data = self.event_loop.run_until_complete(
            tulip.Task(self.reader.read()))
        self.assertEqual(b'', data)


class LengthStreamReaderTests(unittest.TestCase):

    def setUp(self):
        self.stream = protocol.HttpStreamReader()
        self.event_loop = tulip.new_event_loop()
        tulip.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_read(self):
        reader = protocol.LengthStreamReader(self.stream, 8)
        self.stream.feed_data(b'data')
        self.stream.feed_data(b'data')

        data = self.event_loop.run_until_complete(tulip.Task(reader.read()))
        self.assertEqual(b'datadata', data)
        self.assertEqual(0, reader.length)

        data = self.event_loop.run_until_complete(tulip.Task(reader.read()))
        self.assertEqual(b'', data)

    def test_read_zero(self):
        reader = protocol.LengthStreamReader(self.stream, 0)
        self.stream.feed_data(b'data')

        data = self.event_loop.run_until_complete(tulip.Task(reader.read()))
        self.assertEqual(b'', data)

        data = self.event_loop.run_until_complete(
            tulip.Task(self.stream.read(4)))
        self.assertEqual(b'data', data)


class EofStreamReaderTests(unittest.TestCase):

    def setUp(self):
        self.stream = protocol.HttpStreamReader()
        self.event_loop = tulip.new_event_loop()
        tulip.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_read(self):
        reader = protocol.EofStreamReader(self.stream)
        self.stream.feed_data(b'data')
        self.stream.feed_eof()

        data = self.event_loop.run_until_complete(tulip.Task(reader.read()))
        self.assertEqual(b'data', data)

        data = self.event_loop.run_until_complete(tulip.Task(reader.read()))
        self.assertEqual(b'', data)


class BodyTests(unittest.TestCase):

    def setUp(self):
        self.stream = protocol.HttpStreamReader()
        self.reader = protocol.LengthStreamReader(self.stream, 4)
        self.event_loop = tulip.new_event_loop()
        tulip.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()

    def test_unknown_mode(self):
        self.assertRaises(
            ValueError, protocol.Body, self.reader, 'unknown')

    def test_plain_data(self):
        self.stream.feed_data(b'dataline')

        body = protocol.Body(self.reader)
        self.assertIsNone(body.dec)

        data = self.event_loop.run_until_complete(tulip.Task(body.read()))
        self.assertEqual(b'data', data)
        self.assertTrue(body.finished)

        data2 = self.event_loop.run_until_complete(tulip.Task(body.read()))
        self.assertEqual(b'data', data2)

    def test_gzip(self):
        data = gzip.compress(b'data')
        reader = protocol.LengthStreamReader(self.stream, len(data))

        self.stream.feed_data(data)

        body = protocol.Body(reader, 'gzip')
        self.assertIsNotNone(body.dec)

        data = self.event_loop.run_until_complete(tulip.Task(body.read()))
        self.assertEqual(b'data', data)

    def test_deflate(self):
        data = zlib.compress(b'data')
        reader = protocol.LengthStreamReader(self.stream, len(data))

        self.stream.feed_data(data)

        body = protocol.Body(reader, 'deflate')
        self.assertIsNotNone(body.dec)

        data = self.event_loop.run_until_complete(tulip.Task(body.read()))
        self.assertEqual(b'data', data)

    def test_compress_error(self):
        data = gzip.compress(b'data')
        reader = protocol.LengthStreamReader(self.stream, 4)
        self.stream.feed_data(data)

        body = protocol.Body(reader, 'gzip')
        self.assertIsNotNone(body.dec)

        data = self.event_loop.run_until_complete(tulip.Task(body.read()))
        self.assertEqual(data[:4], data)


class HttpClientProtocolTests(unittest.TestCase):

    def test_protocol(self):
        transport = unittest.mock.Mock()

        p = protocol.HttpClientProtocol()
        p.connection_made(transport)
        self.assertIs(p.transport, transport)
        self.assertIsInstance(p.rstream, protocol.HttpStreamReader)
        self.assertIsInstance(p.wstream, protocol.StreamWriter)

        p.data_received(b'data')
        self.assertEqual(4, p.rstream.byte_count)

        p.eof_received()
        self.assertTrue(p.rstream.eof)

        p.connection_lost(None)
