"""http server classes."""

__all__ = ['ServerHttpProtocol', 'WSGIServerHttpProtocol']

import http.server
import io
import logging
import os
import sys
import time
import tulip
import traceback
from email.utils import formatdate
from urllib.parse import unquote, urlsplit

from .protocol import HttpProtocol

SERVER_SOFTWARE = 'tulip/0.0'


class HTTPException(Exception):

    def __init__(self, code, message=''):
        self.code = code
        self.message = message


class ServerHttpProtocol(HttpProtocol):

    # TODO: config
    # TODO: use HTTPException

    debug = False
    handler = None
    request_count = 0
    closing = False

    RESPONSES = http.server.BaseHTTPRequestHandler.responses
    DEFAULT_ERROR_MESSAGE = """
<html>
  <head>
    <title>%(status)s %(reason)s</title>
  </head>
  <body>
    <h1>%(status)s %(reason)s</h1>
    %(mesg)s
  </body>
</html>"""

    def data_received(self, data):
        if self.handler is None:
            self.handler = tulip.Task(self.handle())

        self.rstream.feed_data(data)

    def connection_lost(self, exc):
        if self.handler and not self.handler.done():
            self.handler.cancel()
            self.handler = None

    def close(self):
        self.closing = True

    @tulip.coroutine
    def handle(self):
        rline = None
        message = None
        self.request_count += 1

        try:
            try:
                rline = yield from self.rstream.read_request_line()
                message = yield from self.rstream.read_message(
                    rline.version, readall=False)
            except tulip.CancelledError:
                raise
            except Exception as exc:
                self.handle_error(400, rline, message, exc)
                self.close()
            else:
                yield from self.handle_one_request(rline, message)

        except tulip.CancelledError:
            logging.debug("Ignored premature client disconnection.")
        except Exception as exc:
            self.handle_error(500, rline, message, exc)
            self.close()
        finally:
            self.handler = None
            if self.closing:
                self.transport.close()

    def handle_error(self, status=500, rline=None, message=None, exc=None):
        try:
            reason, mesg = self.RESPONSES[status]
        except KeyError:
            reason, mesg = '???', ''

        if self.debug and exc is not None:
            tb = traceback.format_exc()
            mesg += '<br><h2>Traceback:</h2>\n<pre>%s</pre>' % tb

        if status == 500:
            logging.exception("Error handling request")

        html = self.DEFAULT_ERROR_MESSAGE % {
            'status': status, 'reason': reason, 'mesg': mesg}

        headers = ('HTTP/1.1 %s %s\r\n'
                   'Connection: close\r\n'
                   'Content-Type: text/html\r\n'
                   'Content-Length: %d\r\n'
                   '\r\n' % (str(status), reason, len(html)))
        self.wstream.write_str(headers + html)

    def handle_one_request(self, rline, request):
        raise NotImplementedError


class WSGIServerHttpProtocol(ServerHttpProtocol):

    # TODO: support EXPECT header

    SCRIPT_NAME = os.environ.get('SCRIPT_NAME', '')

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)

        self.wsgi = app  # move to config

    def create_wsgi_response(self, rline, close):
        return Response(rline, close, self.wstream)

    def create_wsgi_environ(self, rline, headers, payload):
        url_scheme = 'http'
        uri_parts = urlsplit(rline.uri)

        environ = {
            'wsgi.input': payload,
            'wsgi.errors': sys.stderr,
            'wsgi.version': (1, 0),
            'wsgi.multithread': False,
            'wsgi.multiprocess': False,
            'wsgi.run_once': False,
            'wsgi.file_wrapper': FileWrapper,
            'wsgi.url_scheme': url_scheme,
            'SERVER_SOFTWARE': SERVER_SOFTWARE,
            'REQUEST_METHOD': rline.method,
            'QUERY_STRING': uri_parts.query or '',
            'RAW_URI': rline.uri,
            'SERVER_PROTOCOL': 'HTTP/%s.%s' % rline.version
        }

        # authors should be aware that REMOTE_HOST and REMOTE_ADDR
        # may not qualify the remote addr:
        # http://www.ietf.org/rfc/rfc3875
        forward = self.transport.get_extra_info('addr', '127.0.0.1')
        script_name = self.SCRIPT_NAME

        for hdr_name, hdr_value in headers:
            if hdr_name == 'EXPECT':
                # handle expect
                #if hdr_value.lower() == "100-continue":
                #    sock.send("HTTP/1.1 100 Continue\r\n\r\n")
                pass
            elif hdr_name == 'HOST':
                server = hdr_value
            elif hdr_name == 'SCRIPT_NAME':
                script_name = hdr_value
            elif hdr_name == 'CONTENT-TYPE':
                environ['CONTENT_TYPE'] = hdr_value
                continue
            elif hdr_name == 'CONTENT-LENGTH':
                environ['CONTENT_LENGTH'] = hdr_value
                continue

            key = 'HTTP_' + hdr_name.replace('-', '_')
            if key in environ:
                hdr_value = "%s,%s" % (environ[key], hdr_value)

            environ[key] = hdr_value

        if isinstance(forward, str):
            # we only took the last one
            # http://en.wikipedia.org/wiki/X-Forwarded-For
            if ',' in forward:
                forward = forward.rsplit(",", 1)[1].strip()

            # find host and port on ipv6 address
            if '[' in forward and ']' in forward:
                host = forward.split(']')[0][1:].lower()
            elif ':' in forward and forward.count(':') == 1:
                host = forward.split(':')[0].lower()
            else:
                host = forward

            forward = forward.split(']')[-1]
            if ':' in forward and forward.count(':') == 1:
                port = forward.split(':', 1)[1]
            else:
                port = 80

            remote = (host, port)
        else:
            remote = forward

        environ['REMOTE_ADDR'] = remote[0]
        environ['REMOTE_PORT'] = str(remote[1])

        if isinstance(server, str):
            server = server.split(':')
            if len(server) == 1:
                if url_scheme == 'http':
                    server.append('80')
                elif url_scheme == 'https':
                    server.append('443')
                else:
                    server.append('')

        environ['SERVER_NAME'] = server[0]
        environ['SERVER_PORT'] = str(server[1])

        path_info = uri_parts.path
        if script_name:
            path_info = path_info.split(script_name, 1)[1]

        environ['PATH_INFO'] = unquote(path_info)
        environ['SCRIPT_NAME'] = script_name

        return environ

    @tulip.coroutine
    def handle_one_request(self, rline, message):
        payload = io.BytesIO((yield from message.payload))

        environ = {}
        response = None
        try:
            r_start = time.monotonic()

            environ = self.create_wsgi_environ(rline, message.headers, payload)
            response = self.create_wsgi_response(rline, message.close)

            environ['tulip.reader'] = self.rstream
            environ['tulip.writer'] = self.wstream

            respiter = self.wsgi(environ, response.start_response)
            if isinstance(respiter, tulip.Future):
                respiter = yield from respiter

            try:
                # TODO: use resp.write_file
                for item in respiter:
                    response.write(item)

                response.close()

                r_duration = time.monotonic() - r_start
            finally:
                if hasattr(respiter, "close"):
                    respiter.close()

        finally:
            if response is not None:
                if response.should_close():
                    self.close()
            else:
                self.close()


class FileWrapper:

    def __init__(self, filelike, chunk_size=8192):
        self.filelike = filelike
        self.chunk_size = chunk_size
        if hasattr(filelike, 'close'):
            self.close = filelike.close

    def __iter__(self):
        return self

    def __next__(self, key):
        data = self.filelike.read(self.chunk_size)
        if data:
            return data
        raise StopIteration


class Response:

    # TODO: url_scheme fix

    HOP_HEADERS = {
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailers',
        'transfer-encoding',
        'upgrade',
        'server',
        'date'}

    def __init__(self, rline, close, wstream):
        self.rline = rline
        self.wstream = wstream
        self.status = None
        self.chunked = False
        self.closing = close
        self.headers = []
        self.headers_sent = False
        self.length = None
        self.sent = 0
        self.upgrade = False

    def force_close(self):
        self.closing = True

    def should_close(self):
        if self.closing:
            return True
        if self.length is not None or self.chunked:
            return False
        return True

    def start_response(self, status, headers, exc_info=None):
        if exc_info:
            try:
                if self.status and self.headers_sent:
                    raise exc_info[1]
            finally:
                exc_info = None

        elif self.status is not None:
            raise AssertionError("Response headers already set!")

        self.status = status
        self.process_headers(headers)

        # Only use chunked responses when the client is
        # speaking HTTP/1.1 or newer and there was
        # no Content-Length header set.
        # Do not use chunked responses when the response is guaranteed to
        # not have a response body (304, 204).
        if (self.length is None and
                self.rline.version > (1, 0) and
                not self.status.startswith(('304', '204'))):
            self.chunked = True

        return self.write

    def process_headers(self, headers):
        for name, value in headers:
            assert isinstance(name, str), "%r is not a string" % name

            name = name.strip()
            lname = name.lower()
            value = str(value).strip()

            if lname == "content-length":
                self.length = int(value)

            elif lname in self.HOP_HEADERS:
                if lname == "connection":
                    # handle websocket
                    if value.lower() == "upgrade":
                        self.upgrade = True
                elif lname == "upgrade":
                    if value.lower() == "websocket":
                        self.headers.append((name.strip(), value))

                # ignore hopbyhop headers
                continue

            self.headers.append((name, value))

    def get_default_headers(self):
        # set the connection header
        if self.upgrade:
            connection = "upgrade"
        elif self.should_close():
            connection = "close"
        else:
            connection = "keep-alive"

        headers = [
            "HTTP/{0[0]}.{0[1]} {1}\r\n".format(
                self.rline.version, self.status),
            "Server: %s\r\n" % SERVER_SOFTWARE,
            "Date: %s\r\n" % formatdate(),
            "Connection: %s\r\n" % connection
        ]
        if self.chunked:
            headers.append("Transfer-Encoding: chunked\r\n")

        return headers

    def send_headers(self):
        if self.headers_sent:
            return

        tosend = self.get_default_headers()
        tosend.extend(['%s: %s\r\n' % (k, v) for k, v in self.headers])

        self.wstream.write_str('%s\r\n' % ''.join(tosend))
        self.headers_sent = True

    def write(self, arg):
        self.send_headers()

        arglen = len(arg)
        tosend = arglen
        if self.length is not None:
            if self.sent >= self.length:
                # Never write more than self.response_length bytes
                return

            tosend = min(self.length - self.sent, tosend)
            if tosend < arglen:
                arg = arg[:tosend]

        # Sending an empty chunk signals the end of the
        # response and prematurely closes the response
        if self.chunked and tosend == 0:
            return

        self.sent += tosend
        if self.chunked:
            self.wstream.write_chunked(arg)
        else:
            self.wstream.write(arg)

    def write_file(self, respiter):
        for item in respiter:
            self.write(item)

    def close(self):
        if not self.headers_sent:
            self.send_headers()

        if self.chunked:
            self.wstream.write_chunked_eof()
