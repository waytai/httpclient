"""test http server."""

import cgi
import email
import email.parser
import email.utils
import http.server
import http.client
import json
import io
import logging
import re
import sys
import urllib.parse
import traceback

from . import server


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
            return TestServerProtocol(self, router)
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


class TestServerProtocol(server.WSGIServerHttpProtocol):

    def __init__(self, server, router):
        super().__init__(router())

        self.server = server

    def create_wsgi_environ(self, rline, message, payload):
        environ = super().create_wsgi_environ(rline, message, payload)
        environ['s_params'] = (
            self.server, self.transport, self.wstream,
            rline, message.headers, payload, message.compression)

        return environ

    def handle_one_request(self, rline, message):
        if self.server.noresponse:
            return

        yield from super().handle_one_request(rline, message)


class Router:

    _response_version = "HTTP/1.1"
    _responses = http.server.BaseHTTPRequestHandler.responses

    def __call__(self, environ, start_response):
        self._environ = environ
        self._start_response = start_response

        (server, transport, stream,
         rline, headers, body, cmode) = environ['s_params']

        # headers
        self._headers = http.client.HTTPMessage()
        for hdr, val in headers:
            self._headers[hdr] = val

        self._server = server
        self._transport = transport
        self._stream = stream
        self._method = rline.method
        self._uri = rline.uri
        self._version = rline.version
        self._compression = cmode
        self._body = body.read()

        url = urllib.parse.urlsplit(self._uri)
        self._path = url.path
        self._query = url.query

        self.dispatch()
        return []

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

                return

        return self._response(404)

    def _response(self, code, body=None,
                  headers=None, writers=(), chunked=False):
        r_headers = {}
        for key, val in self._headers.items():
            key = '-'.join(p.capitalize() for p in key.split('-'))
            r_headers[key] = val

        encoding = self._headers.get('content-encoding', '').lower()
        if 'gzip' in encoding:
            cmod = 'gzip'
        elif 'deflate' in encoding:
            cmod = 'deflate'
        else:
            cmod = ''

        resp = {
            'method': self._method,
            'version': '%s.%s' % self._version,
            'path': self._uri,
            'headers': r_headers,
            'origin': self._transport.get_extra_info('addr', ' ')[0],
            'query': self._query,
            'form': {},
            'compression': cmod,
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

        # default headers
        hdrs = [('Connection', 'close'),
                ('Content-Type', 'application/json')]
        if chunked:
            hdrs.append(('Transfer-Encoding', 'chunked'))
        else:
            hdrs.append(('Content-Length', str(len(body))))

        # extra headers
        if headers:
            hdrs.extend(headers.items())

        # write status
        write = self._start_response(
            '%s %s' % (code, self._responses[code][0]), hdrs)

        write(str_to_bytes(body))  # writers
