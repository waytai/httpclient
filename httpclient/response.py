"""Http response"""

import http.client
import io
import json
import re
from tulip import tasks

from .protocol import ChunkedReader, LengthReader, EofReader


class HttpResponse:

    stream = None
    headers = None

    # from the Status-Line of the response
    version = None  # HTTP-Version
    status = None  # Status-Code
    reason = None  # Reason-Phrase

    content = None
    will_close = None  # conn will close at end of response

    def __init__(self, method, url):
        self.method = method
        self.url = url

    def __repr__(self):
        out = io.StringIO()
        print('<HttpResponse [%s %s]>' % (self.status, self.reason), file=out)
        print(self.headers, file=out)
        return out.getvalue()

    def start(self, stream, readbody=False):
        if self.stream is not None:
            raise RuntimeError('Response is in process.')

        self.stream = stream

        # read status
        self.version, self.status, self.reason = (
            yield from self.stream.read_response_status())

        # does the body have a fixed length? (of zero)
        length = None
        if (self.status == http.client.NO_CONTENT or
                self.status == http.client.NOT_MODIFIED or
                100 <= self.status < 200 or self.method == "HEAD"):
            length = 0

        # http message
        message = yield from self.stream.read_message(length=length)

        # headers
        self.headers = http.client.HTTPMessage()
        for hdr, val in message.headers:
            self.headers[hdr] = val

        self.body = message.payload

        if readbody:
            self.content = yield from message.payload

        return self

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

    def read(self, decode=False):
        if self.content is None:
            self.content = yield from self.body

        data = self.content

        if decode:
            ct = self.headers.get('content-type', '').lower()
            if ct == 'application/json':
                data = json.loads(data.decode('utf-8'))

        return data
