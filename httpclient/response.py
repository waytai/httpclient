"""Http response"""

import http.client
import io
import json

import tulip.http


class HttpResponse:

    stream = None
    transport = None

    # from the Status-Line of the response
    version = None  # HTTP-Version
    status = None  # Status-Code
    reason = None  # Reason-Phrase
    headers = None

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

    def start(self, stream, transport, readbody=False):
        if self.stream is not None:
            raise RuntimeError('Response is in process.')

        self.stream = stream
        self.transport = transport

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
            self.headers.add_header(hdr, val)

        # body
        self.body = message.payload

        if readbody:
            self.content = yield from message.payload.read()

        return self

    def close(self):
        if self.transport:
            self.transport.close()
            self.transport = None

    def isclosed(self):
        return self.transport is None

    def read(self, decode=False):
        if self.content is None:
            self.content = yield from self.body.read()

        data = self.content

        if decode:
            ct = self.headers.get('content-type', '').lower()
            if ct == 'application/json':
                data = json.loads(data.decode('utf-8'))

        return data
