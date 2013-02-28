
__all__ = ['HttpProtocol', 'HttpMessage',
           'HttpStreamReader', 'HttpStreamWriter',
           'ChunkedReader', 'LengthReader', 'EofReader',
           'ChunkedWriter', 'DeflateWriter']

import collections
import http.client
import logging
import re
import zlib
from io import BytesIO

import tulip
import tulip.http

HDRRE = re.compile(b"[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")
METHRE = re.compile("([A-Za-z]+)")
VERSRE = re.compile("HTTP/(\d+).(\d+)")
CONTINUATION = (b' ', b'\t')

RequestLine = collections.namedtuple(
    'RequestLine', ['method', 'uri', 'version'])

ResponseStatus = collections.namedtuple(
    'ResponseStatus', ['version', 'code', 'reason'])

HttpMessage = collections.namedtuple(
    'HttpMessage', ['headers', 'payload', 'close', 'compression'])


class HttpStreamReader(tulip.http.HttpStreamReader):

    MAX_HEADERS = 32768
    MAX_HEADERFIELD_SIZE = 8190

    def __init__(self, transport=None, limit=2**16):
        super().__init__(limit)
        self.transport = transport

    def close(self):
        self.transport.close()

    @tulip.coroutine
    def read_request_line(self):
        """Read request status line. Exception http.client.BadStatusLine
        could be raised in case of any errors in status line.
        Returns three values (method, path, version)

        Example:

            GET /path HTTP/1.1

            >> yield from reader.read_request_line()
            ('GET', '/path', (1, 1))

        """
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

        return RequestLine(method, uri, version)

    @tulip.coroutine
    def read_response_status(self):
        """Read response status line. Exception http.client.BadStatusLine
        could be raised in case of any errors in status line.
        Returns three values (version, status_code, reason)

        Example:

            HTTP/1.1 200 Ok

            >> yield from reader.read_response_status()
            ((1, 1), 200, 'Ok')

        """
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

        return ResponseStatus(version, status, reason.strip())

    @tulip.coroutine
    def read_headers(self):
        """Read and parses RFC2822 headers from a stream. Supports
        line continuation."""
        size = 0
        headers = []

        line = yield from self.readline()

        while line not in (b'\r\n', b'\n'):
            header_length = len(line)

            # Parse initial header name : value pair.
            sep_pos = line.find(b':')
            if sep_pos < 0:
                raise ValueError('Invalid header %s' % line.strip())

            name, value = line[:sep_pos], line[sep_pos+1:]
            name = name.rstrip(b' \t').upper()
            if HDRRE.search(name):
                raise ValueError('Invalid header name %s' % name)

            name, value = name.strip().decode('latin1'), [value.lstrip()]

            # next line
            line = yield from self.readline()

            # consume continuation lines
            continuation = line.startswith(CONTINUATION)

            if continuation:
                while continuation:
                    header_length += len(line)
                    if header_length > self.MAX_HEADERFIELD_SIZE:
                        raise http.client.LineTooLong(
                            "limit request headers fields size")
                    value.append(line)

                    line = yield from self.readline()
                    continuation = line.startswith(CONTINUATION)
            else:
                if header_length > self.MAX_HEADERFIELD_SIZE:
                    raise http.client.LineTooLong(
                        "limit request headers fields size")

            # total headers size
            size += header_length
            if size >= self.MAX_HEADERS:
                raise http.client.LineTooLong("limit request headers fields")

            headers.append((name, b''.join(value).rstrip().decode('latin1')))

        return headers

    @tulip.coroutine
    def read_payload(self, reader, encoding=None):
        if encoding is not None:
            if encoding not in ('gzip', 'deflate'):
                raise ValueError(
                    'Content-Encoding is not supported %r' % encoding)

            zlib_mode = (16 + zlib.MAX_WBITS
                         if encoding == 'gzip' else -zlib.MAX_WBITS)

            dec = zlib.decompressobj(zlib_mode)
        else:
            dec = None

        buf = BytesIO()

        while True:
            chunk = yield from reader.read(self)
            if not chunk:
                if dec is not None:
                    buf.write(dec.flush())
                break

            if dec is not None:
                try:
                    chunk = dec.decompress(chunk)
                except:
                    pass

            buf.write(chunk)

        return buf.getvalue()

    @tulip.coroutine
    def read_message(self, version=(1, 1),
                     length=None, compression=True, readall=True):
        # load headers
        headers = yield from self.read_headers()

        # payload params
        chunked = False
        content_length = length
        cmode = None
        close_conn = None

        for (name, value) in headers:
            if name == 'CONTENT-LENGTH':
                content_length = value
            elif name == 'TRANSFER-ENCODING':
                chunked = 'chunked' in value.lower()
            elif name == 'SEC-WEBSOCKET-KEY1':
                content_length = 8
            elif name == "CONNECTION":
                v = value.lower()
                if v == "close":
                    close_conn = True
                elif v == "keep-alive":
                    close_conn = False
            elif name == 'CONTENT-ENCODING' and compression:
                enc = value.lower()
                if 'gzip' in enc:
                    cmode = 'gzip'
                elif 'deflate' in enc:
                    cmode = 'deflate'

        if close_conn is None:
            close_conn = version <= (1, 0)

        # payload
        if chunked:
            payload = self.read_payload(ChunkedReader(), cmode)

        elif content_length is not None:
            try:
                content_length = int(content_length)
            except ValueError:
                raise ValueError('CONTENT-LENGTH')

            if content_length < 0:
                raise ValueError('CONTENT-LENGTH')

            payload = self.read_payload(LengthReader(content_length), cmode)
        else:
            if readall:
                payload = self.read_payload(EofReader(), cmode)
            else:
                payload = self.read_payload(LengthReader(0), cmode)

        return HttpMessage(headers, payload, close_conn, cmode)


class HttpStreamWriter:

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

    def write_chunked(self, chunk):
        if not chunk:
            return
        data = self.encode(chunk)
        self.write_str('{:x}\r\n'.format(len(data)))
        self.transport.write(data)
        self.transport.write(b'\r\n')

    def write_chunked_eof(self):
        self.transport.write(b'0\r\n\r\n')

    def write_body(self, data, writers=None, chunked=False):
        if writers:
            for wrt in writers:
                data = wrt.write(data)
        elif isinstance(data, bytes):
            data = (data,)

        if chunked:
            for chunk in data:
                self.write_chunked(chunk)
            self.write_chunked_eof()
        else:
            for chunk in data:
                self.write(chunk)


class ChunkedReader:

    @tulip.coroutine
    def read(self, stream):
        while True:
            try:
                size = yield from self._read_next_chunk_size(stream)
                if not size:
                    break
            except ValueError:
                raise http.client.IncompleteRead(b'')

            # read chunk
            data = yield from stream.readexactly(size)

            # toss the CRLF at the end of the chunk
            crlf = yield from stream.readexactly(2)

            return data

        # read and discard trailer up to the CRLF terminator
        while True:
            line = yield from stream.readline()
            if line in (b'\r\n', b'\n', b''):
                break

        return b''

    def _read_next_chunk_size(self, stream):
        # Read the next chunk size from the file
        line = yield from stream.readline()

        i = line.find(b";")
        if i >= 0:
            line = line[:i]  # strip chunk-extensions
        try:
            return int(line, 16)
        except ValueError:
            raise


class LengthReader:

    def __init__(self, length):
        self.length = length

    @tulip.coroutine
    def read(self, stream):
        if self.length:
            data = yield from stream.readexactly(self.length)
            self.length = 0
        else:
            data = b''

        return data


class EofReader:

    @tulip.coroutine
    def read(self, stream):
        return (yield from stream.read())


class ChunkedWriter:

    def __init__(self, chunk_size=None):
        self.chunk_size = chunk_size

    def write(self, stream):
        if isinstance(stream, bytes):
            stream = (stream,)
        stream = iter(stream)

        buf = BytesIO()
        while True:
            while buf.tell() < self.chunk_size:
                try:
                    data = next(stream)
                    buf.write(data)
                except StopIteration:
                    yield buf.getvalue()
                    return

            data = buf.getvalue()
            chunk, rest = data[:self.chunk_size], data[self.chunk_size:]
            buf = BytesIO()
            buf.write(rest)

            yield chunk


class DeflateWriter:

    def __init__(self, encoding='deflate'):
        zlib_mode = (16 + zlib.MAX_WBITS
                     if encoding == 'gzip' else -zlib.MAX_WBITS)

        self.zlib = zlib.compressobj(wbits=zlib_mode)

    def write(self, stream):
        if isinstance(stream, bytes):
            stream = (stream,)
        stream = iter(stream)

        for chunk in stream:
            yield self.zlib.compress(chunk)

        yield self.zlib.flush()


class HttpProtocol(tulip.Protocol):

    transport = None
    rstream = None
    wstream = None

    def __init__(self, encoding='utf-8'):
        self.encoding = encoding

    def connection_made(self, transport):
        self.transport = transport
        self.rstream = HttpStreamReader(transport)
        self.wstream = HttpStreamWriter(transport, self.encoding)

    def data_received(self, data):
        self.rstream.feed_data(data)

    def eof_received(self):
        self.rstream.feed_eof()

    def connection_lost(self, exc):
        pass
