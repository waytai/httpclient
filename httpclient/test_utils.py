"""test http server."""

import cgi
import collections
import email
import email.parser
import http.server
import json
import io
import logging
import re
import sys
import time
import tulip
import urllib.parse
import traceback

from . import protocol


def str_to_bytes(s):
    if isinstance(s, bytes):
        return s
    return s.encode('latin1')


class HttpServer:

    noresponse = False

    def __init__(self, router, loop, host='127.0.0.1', port=8080):
        self.loop = loop
        self.host = host
        self.port = port
        self.props = {}
        self._url = 'http://%s:%s' % (host, port)

        def protocol():
            return HttpServerProtocol(self, router)
        self.protocol = protocol

    def get(self, name, default=None):
        return self.props.get(name, default)

    def __getitem__(self, name):
        return self.props[name]

    def __setitem__(self, name, value):
        self.props[name] = value

    def url(self, *suffix):
        return urllib.parse.urljoin(
            self._url, '/'.join(str(s) for s in suffix))

    def start(self):
        return self.loop.start_serving(self.protocol, self.host, self.port)


class HttpServerProtocol(tulip.Protocol):

    def __init__(self, server, router):
        super().__init__()

        self.server = server
        self.Router = router
        self.transport = None
        self.rstream = None
        self.wstream = None
        self.handler = None

    def connection_made(self, transport):
        self.transport = transport
        self.rstream = protocol.HttpStreamReader(transport)
        self.wstream = protocol.HttpStreamWriter(transport)
        self.handler = self.handle_request()

    def data_received(self, data):
        self.rstream.feed_data(data)

    def eof_received(self):
        self.rstream.feed_eof()

    def connection_lost(self, exc):
        pass

    @tulip.task
    def handle_request(self):
        method, path, version = yield from self.rstream.read_request_status()
        headers = yield from self.rstream.read_headers()

        # content encoding
        enc = headers.get('content-encoding', '').lower()
        if 'gzip' in enc:
            mode = 'gzip'
        elif 'deflate' in enc:
            mode = 'deflate'
        else:
            mode = None

        # are we using the chunked-style of transfer encoding?
        tr_enc = headers.get("transfer-encoding")
        if tr_enc and tr_enc.lower() == "chunked":
            chunked = True
        else:
            chunked = False

        # length
        if not chunked and 'content-length' in headers:
            try:
                length = int(headers.get("content-length"))
            except ValueError:
                raise ValueError("Invalid header: CONTENT-LENGTH")
            else:
                if length < 0:
                    length = None
        else:
            length = None

        # body
        if chunked:
            body = self.rstream.read_body(protocol.ChunkedReader(), mode)
        elif length is not None:
            body = self.rstream.read_body(protocol.LengthReader(length), mode)
        else:
            body = self.rstream.read_body(protocol.EofReader(), mode)

        body = yield from body

        if self.server.noresponse:
            return

        try:
            router = self.Router(
                self.server, self.transport, self.wstream,
                (method, path, version, mode), headers, body)
            router.dispatch()
        except:
            out = io.StringIO()
            traceback.print_exc(file=out)
            traceback.print_exc()
            router._response(500, out.getvalue())

        self.transport.close()


class Router:

    _response_version = "HTTP/1.1"
    _responses = http.server.BaseHTTPRequestHandler.responses

    _weekdayname = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    _monthname = [None,
                  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    def __init__(self, server, transport, stream, status, headers, body):
        self._server = server
        self._transport = transport
        self._stream = stream
        self._method = status[0]
        self._full_path = status[1]
        self._version = status[2]
        self._compression = status[3]
        self._headers = headers
        self._body = body

        url = urllib.parse.urlsplit(self._full_path)
        self._path = url.path
        self._query = url.query

    @staticmethod
    def define(rmatch):
        def wrapper(fn):
            f_locals = sys._getframe(1).f_locals
            mapping = f_locals.setdefault('_mapping', [])
            mapping.append((re.compile(rmatch), fn.__name__))
            return fn

        return wrapper

    def dispatch(self):
        for route, fn in self._mapping:
            match = route.match(self._path)
            if match is not None:
                try:
                    return getattr(self, fn)(match)
                except:
                    out = io.StringIO()
                    traceback.print_exc(file=out)
                    self._response(500, out.getvalue())

        return self._response(404)

    def _response(self, code, body=None,
                  headers=None, writers=(), chunked=False):
        r_headers = {}
        for key, val in self._headers.items():
            key = '-'.join(p.capitalize() for p in key.split('-'))
            r_headers[key] = val

        resp = {
            'method': self._method,
            'version': '%s.%s' % self._version,
            'path': self._full_path,
            'headers': r_headers,
            'origin': self._transport.get_extra_info('addr', ' ')[0],
            'query': self._query,
            'form': {},
            'compression': self._compression,
            'multipart-data': []
        }
        if body:
            resp['content'] = body

        ct = self._headers.get('content-type', '').lower()

        # application/x-www-form-urlencoded
        if ct == 'application/x-www-form-urlencoded':
            resp['form'] = urllib.parse.parse_qs(self._body.decode('latin1'))

        # multipart/form-data
        elif ct.startswith('multipart/form-data'):
            out = io.BytesIO()
            for key, val in self._headers.items():
                out.write(bytes('{}: {}\r\n'.format(key, val), 'latin1'))

            out.write(b'\r\n')
            out.write(self._body)
            out.write(b'\r\n')
            out.seek(0)

            message = email.parser.BytesParser().parse(out)
            if message.is_multipart():
                for msg in message.get_payload():
                    if msg.is_multipart():
                        logging.warn('multipart msg is not expected')
                    else:
                        key, params = cgi.parse_header(
                            msg.get('content-disposition', ''))
                        params['data'] = msg.get_payload()
                        params['content-type'] = msg.get_content_type()
                        resp['multipart-data'].append(params)

        body = json.dumps(resp, indent=4, sort_keys=True)

        write = self._stream.write

        # write status
        write(str_to_bytes('%s %s %s\r\n' % (
            self._response_version, code,
            self._responses[code][0])))

        # date
        timestamp = time.time()
        year, month, day, hh, mm, ss, wd, y, z = time.gmtime(timestamp)
        date = "%s, %02d %3s %4d %02d:%02d:%02d GMT" % (
            self._weekdayname[wd],
            day, self._monthname[month], year, hh, mm, ss)

        write(str_to_bytes('Date: %s\r\n' % date))

        # default headers
        write(b'Connection: close\r\n')
        write(b'Content-Type: application/json\r\n')
        write(str_to_bytes('Content-Length: %s\r\n' % len(body)))

        # extra headers
        if headers:
            for hdr, val in headers.items():
                write(str_to_bytes('%s: %s\r\n' % (hdr, str(val).strip())))

        write(b'\r\n')

        self._stream.write_body(str_to_bytes(body), writers, chunked)
