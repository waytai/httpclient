
__all__ = ['HttpProtocol',
           'HttpStreamReader', 'HttpStreamWriter',
           'ChunkedWriter', 'LengthWriter', 'EofWriter']

import tulip
import tulip.http


class HttpStreamReader(tulip.http.HttpStreamReader):

    def __init__(self, transport=None, limit=2**16):
        super().__init__(limit)
        self.transport = transport

    def close(self):
        self.transport.close()


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
