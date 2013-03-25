"""http server classes.

TODO:
  * config
  * support EXPECT header
  * url_scheme fix (Response)
  * Proxy protocol
  * x-forward sec
  * wsgi file support

"""

__all__ = ['WSGIServerHttpProtocol']

import http.server
import io
import itertools
import logging
import os
import sys
import traceback
from email.utils import formatdate
from urllib.parse import unquote, urlsplit

import tulip
import tulip.http

from . import protocol


class WSGIServerHttpProtocol(tulip.http.ServerHttpProtocol):

    SCRIPT_NAME = os.environ.get('SCRIPT_NAME', '')

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)

        self.wsgi = app  # move to config

    def create_wsgi_response(self, rline, close):
        return Response(self.wstream, rline, close)

    def create_wsgi_environ(self, rline, message, payload):
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

        for hdr_name, hdr_value in message.headers:
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

        environ['tulip.reader'] = self.rstream
        environ['tulip.writer'] = self.wstream

        return environ

    @tulip.coroutine
    def handle_request(self, info, message):
        payload = io.BytesIO((yield from message.payload.read()))

        environ = self.create_wsgi_environ(info, message, payload)
        response = self.create_wsgi_response(info, message.should_close)
        environ['tulip.response'] = response

        respiter = self.wsgi(environ, response.start_response)
        if isinstance(respiter, tulip.Future):
            respiter = yield from respiter

        try:
            for item in respiter:
                response.writer.write(item)

            response.write_eof()
        finally:
            if hasattr(respiter, "close"):
                respiter.close()

        if response.should_close():
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

    status = None
    writer = None

    def __init__(self, stream, rline, close):
        self.stream = stream
        self.rline = rline
        self.close = close

    def should_close(self):
        return self.response.should_close()

    def start_response(self, status, headers, exc_info=None):
        assert self.status is None, 'Response headers already set!'

        if exc_info:
            try:
                if self.status:
                    raise exc_info[1]
            finally:
                exc_info = None

        status_code = int(status.split(' ', 1)[0])

        self.status = status
        self.response = tulip.http.Response(
            self.transport, status_code, self.rline.version, self.close)
        self.response.add_headers(*headers)
        self.response._send_headers = True
        return self.response.write

    def write_eof(self):
        self.response.eof()
