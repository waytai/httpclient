"""Http response"""

import http.client
import io
import re
from tulip import tasks

from .protocol import Body
from .protocol import ChunkedStreamReader, LengthStreamReader, EofStreamReader


class HttpResponse:

    stream = None
    headers = None

    # from the Status-Line of the response
    version = None  # HTTP-Version
    status = None  # Status-Code
    reason = None  # Reason-Phrase

    will_close = None  # conn will close at end of response

    def __init__(self, method, url):
        self.method = method
        self.url = url

    def __str__(self):
        out = io.StringIO()
        print('<HttpResponse [%s %s]>' % (self.status, self.reason), file=out)
        print(self.headers, file=out)
        return out.getvalue()

    def begin(self, stream):
        if self.stream is not None:
            raise RuntimeError('Response is in process.')

        self.stream = stream

        # read status
        version, self.status, self.reason = (
            yield from self.stream.read_response_status())

        if version < (1, 1):
            self.version = 10
        else:
            self.version = 11

        # read headers
        self.headers = yield from self.stream.read_headers()

        # are we using the chunked-style of transfer encoding?
        tr_enc = self.headers.get("transfer-encoding")
        if tr_enc and tr_enc.lower() == "chunked":
            chunked = True
        else:
            chunked = False

        # does the body have a fixed length? (of zero)
        if (self.status == http.client.NO_CONTENT or
            self.status == http.client.NOT_MODIFIED or
            100 <= self.status < 200 or
            self.method == "HEAD"):
            length = 0
        else:
            if not chunked:
                try:
                    length = int(self.headers.get("content-length"))
                except ValueError:
                    raise ValueError("Invalid header: CONTENT-LENGTH")
                else:
                    if length < 0:  # ignore nonsensical negative lengths
                        length = None
            else:
                length = None

        # if the connection remains open, and we aren't using chunked, and
        # a content-length was not provided, then assume that the connection
        # WILL close.
        self.will_close = self._should_close()
        if (not self.will_close and not chunked and length is None):
            self.will_close = True

        # content encoding
        enc = self.headers.get('content-encoding', '').lower()
        if 'gzip' in enc:
            mode = 'gzip'
        elif 'deflate' in enc:
            mode = 'deflate'
        else:
            mode = None

        # body
        if chunked:
            self.body = Body(ChunkedStreamReader(self.stream), mode)
        elif length is not None:
            self.body = Body(LengthStreamReader(self.stream, length), mode)
        else:
            self.body = Body(EofStreamReader(self.stream), mode)

        return self

    def _should_close(self):
        if self.version == 11:
            # An HTTP/1.1 proxy is assumed to stay open unless
            # explicitly closed.
            conn = self.headers.get("connection")
            if conn and "close" in conn.lower():
                return True

            return False

        return self.version == 10

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def isclosed(self):
        # NOTE: it is possible that we will not ever call self.close(). This
        #       case occurs when will_close is TRUE, length is None, and we
        #       read up to the last byte, but NOT past it.
        #
        # IMPLIES: if will_close is FALSE, then self.close() will ALWAYS be
        #          called, meaning self.isclosed() is meaningful.
        return self.stream is None

    def read(self):
        return (yield from self.body.read())
