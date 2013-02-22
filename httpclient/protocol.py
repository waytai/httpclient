
__all__ = ['HttpStreamReader', 'StreamWriter', 'HttpClientProtocol',
           'Body',
           'ChunkedStreamReader', 'LengthStreamReader', 'EofStreamReader']

import http.client
import re
import gzip
import zlib
from io import BytesIO

from tulip import http_client
from tulip import tasks

HDRRE = re.compile("[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")
METHRE = re.compile("([A-Za-z]+)")
VERSRE = re.compile("HTTP/(\d+).(\d+)")


class HttpStreamReader(http_client.StreamReader):

    MAX_HEADERS = 32768
    MAX_HEADERFIELD_SIZE = 8190

    def __init__(self, transport=None, limit=2**16):
        super().__init__(limit)
        self.transport = transport

    def close(self):
        self.transport.close()

    @tasks.coroutine
    def read_request_status(self):
        line = str((yield from self.readline()), 'latin1').strip()

        try:
            method, uri, version = line.split(None, 2)
        except ValueError:
            raise http.client.BadStatusLine(line)

        # method
        if not METHRE.match(method):
            raise http.client.BadStatusLine(method)
        method = method.upper()

        # uri, when the path starts with //, considers it as an absolute url
        if uri.startswith('//'):
            uri = uri[1:]

        # version
        match = VERSRE.match(version)
        if match is None:
            raise http.client.BadStatusLine(version)
        version = (int(match.group(1)), int(match.group(2)))

        return method, uri, version

    @tasks.coroutine
    def read_response_status(self):
        line = str((yield from self.readline()), 'latin1').strip()
        if not line:
            # Presumably, the server closed the connection before
            # sending a valid response.
            raise http.client.BadStatusLine(line)

        try:
            version, status, reason = line.split(None, 2)
        except ValueError:
            try:
                version, status = line.split(None, 1)
                reason = ''
            except ValueError:
                version = ''

        # version
        match = VERSRE.match(version)
        if match is None:
            raise http.client.BadStatusLine(line)
        version = (int(match.group(1)), int(match.group(2)))

        # The status code is a three-digit number
        try:
            status = int(status)
            if status < 100 or status > 999:
                raise http.client.BadStatusLine(line)
        except ValueError:
            raise http.client.BadStatusLine(line)

        return version, status, reason.strip()

    @tasks.coroutine
    def read_headers(self):
        """Read and parses RFC2822 headers from a stream."""
        size = 0
        headers = []
        while True:
            line = yield from self.readline()
            if line in (b'\r\n', b'\n', b''):
                break

            size += len(line)
            if size > self.MAX_HEADERS:
                raise http.client.LineTooLong("max buffer headers")

            headers.append(str(line, 'latin1'))

        message = http.client.HTTPMessage()
        for name, value in self._parse_headers(headers):
            message[name] = value

        return message

    def _parse_headers(self, lines):
        size = 0
        headers = []

        # Parse headers into key/value pairs paying attention
        # to continuation lines.
        while len(lines):
            if size >= self.MAX_HEADERS:
                raise http.client.LineTooLong("limit request headers fields")

            # Parse initial header name : value pair.
            curr = lines.pop(0)
            header_length = len(curr)
            sep_pos = curr.find(":")
            if sep_pos < 0:
                raise ValueError('Invalid header %r' % curr.strip())

            name, value = curr[:sep_pos], curr[sep_pos+1:]
            name = name.rstrip(" \t").upper()
            if HDRRE.search(name):
                raise ValueError('Invalid header name %r' % name)

            name, value = name.strip(), [value.lstrip()]

            # Consume value continuation lines
            while len(lines) and lines[0].startswith((" ", "\t")):
                curr = lines.pop(0)
                header_length += len(curr)
                if header_length > self.MAX_HEADERFIELD_SIZE > 0:
                    raise http.client.LineTooLong(
                        "limit request headers fields size")
                value.append(curr)

            value = ''.join(value).rstrip()

            if header_length > self.MAX_HEADERFIELD_SIZE > 0:
                raise http.client.LineTooLong(
                    "limit request headers fields size")

            size += len(curr)
            headers.append((name, value))

        return headers


class StreamWriter:

    def __init__(self, transport, encoding='utf-8'):
        self.transport = transport
        self.encoding = encoding

    def encode(self, s):
        if isinstance(s, bytes):
            return s
        return s.encode(self.encoding)

    def decode(self, s):
        if isinstance(s, str):
            return s
        return s.decode(self.encoding)

    def write(self, b):
        self.transport.write(b)

    def write_str(self, s):
        self.transport.write(self.encode(s))

    def write_chunked(self, s):
        if not s:
            return
        data = self.encode(s)
        self.write_str('{:x}\r\n'.format(len(data)))
        self.transport.write(data)
        self.transport.write(b'\r\n')

    def write_chunked_eof(self):
        self.transport.write(b'0\r\n\r\n')


class ChunkedStreamReader:

    def __init__(self, stream):
        self.stream = stream
        self.finished = False

    @tasks.coroutine
    def read(self):
        if self.finished:
            return b''

        while True:
            try:
                size = yield from self._read_next_chunk_size()
                if not size:
                    break
            except ValueError:
                raise http.client.IncompleteRead(b'')

            # read chunk
            data = yield from self.stream.readexactly(size)

            # toss the CRLF at the end of the chunk
            crlf = yield from self.stream.readexactly(2)

            return data

        # read and discard trailer up to the CRLF terminator
        while True:
            line = yield from self.stream.readline()
            if line in (b'\r\n', b'\n', b''):
                break

        self.finished = True
        return b''

    def _read_next_chunk_size(self):
        # Read the next chunk size from the file
        line = yield from self.stream.readline()

        i = line.find(b";")
        if i >= 0:
            line = line[:i]  # strip chunk-extensions
        try:
            return int(line, 16)
        except ValueError:
            raise


class LengthStreamReader:

    def __init__(self, stream, length):
        self.stream = stream
        self.length = length

    @tasks.coroutine
    def read(self):
        if self.length:
            data = yield from self.stream.readexactly(self.length)
            self.length = 0
        else:
            data = b''

        return data


class EofStreamReader:

    def __init__(self, stream):
        self.stream = stream
        self.finished = False

    @tasks.coroutine
    def read(self):
        if self.finished:
            return b''

        self.finished = True
        return (yield from self.stream.read())


class Body:

    def __init__(self, reader, mode=None):
        self.reader = reader
        self.buf = BytesIO()
        self.finished = False

        if mode is not None:
            if mode == 'gzip':
                self.dec = gzip
            elif mode == 'deflate':
                self.dec = zlib
            else:
                raise ValueError(
                    'Decompression mode is not supported %r' % mode)
        else:
            self.dec = None

    def read(self):
        if not self.finished:
            while True:
                chunk = yield from self.reader.read()
                if not chunk:
                    self.finished = True
                    break

                if self.dec is not None:
                    try:
                        chunk = self.dec.decompress(chunk)
                    except:
                        pass

                self.buf.write(chunk)

        return self.buf.getvalue()


class HttpClientProtocol:
    """tulip's Protocol class"""

    def __init__(self, encoding='utf-8'):
        self.encoding = encoding

    def connection_made(self, transport):
        self.transport = transport
        self.rstream = HttpStreamReader(transport)
        self.wstream = StreamWriter(transport, self.encoding)

    def data_received(self, data):
        self.rstream.feed_data(data)

    def eof_received(self):
        self.rstream.feed_eof()

    def connection_lost(self, exc):
        pass
