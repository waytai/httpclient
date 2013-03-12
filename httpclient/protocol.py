
__all__ = ['HttpProtocol', 'HttpMessage',
           'HttpStreamReader', 'HttpStreamWriter',
           'ChunkedReader', 'LengthReader', 'EofReader',
           'ChunkedWriter', 'LengthWriter', 'EofWriter']

import collections
import http.client
import logging
import re
import zlib
from io import BytesIO

import tulip
import tulip.http


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

    def write_payload(self, data, writers=None, chunked=False):
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
    """Send chunked encoded data."""

    def __init__(self, wstream):
        self.closed = False
        self.wstream = wstream

    def write(self, data):
        if not self.closed:
            if data:
                self.wstream.write_chunked(data)
            else:
                self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.wstream.write_chunked_eof()


class LengthWriter:
    """Send only 'length' amount of bytes."""

    def __init__(self, wstream, length):
        self.wstream = wstream
        self.length = length

    def write(self, data):
        if self.length:
            l = len(data)
            if self.length >= l:
                self.wstream.write(data)
            else:
                self.wstream.write(data[:self.length])
            
            self.length = max(0, self.length-l)

    def close(self):
        if self.length:
            self.length = 0


class EofWriter:
    """Just send all data."""

    def __init__(self, wstream):
        self.closed = False
        self.wstream = wstream

    def write(self, data):
        if not self.closed:
            self.wstream.write(data)

    def close(self):
        self.closed = True


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
