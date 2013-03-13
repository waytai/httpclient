"""stream utils"""

import io
import zlib


class ChunkedIter:

    def __init__(self, chunk_size=None):
        self.chunk_size = chunk_size

    def write(self, stream):
        if isinstance(stream, bytes):
            stream = (stream,)
        stream = iter(stream)

        buf = io.BytesIO()
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
            buf = io.BytesIO()
            buf.write(rest)

            yield chunk


class DeflateIter:

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
