"""http request"""

import base64
import collections
import email.message
import http.client
import http.cookies
import io
import itertools
import mimetypes
import os
import uuid
import urllib.parse

import tulip
import tulip.http


class HttpRequest:

    GET_METHODS = {'DELETE', 'GET', 'HEAD', 'OPTIONS'}
    POST_METHODS = {'PATCH', 'POST', 'PUT', 'TRACE'}
    ALL_METHODS = GET_METHODS.union(POST_METHODS)

    DEFAULT_HEADERS = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate',
    }

    body = b''

    def __init__(self, method, url, *,
                 params=None,
                 headers=None,
                 data=None,
                 cookies=None,
                 files=None,
                 auth=None,
                 encoding='utf-8',
                 version=(1, 1),
                 compress=None,
                 chunked=None):
        self.method = method.upper()
        self.encoding = encoding

        if isinstance(version, str):
            v = [l.strip() for l in version.split('.', 1)]
            version = int(v[0]), int(v[1])
        self.version = version

        scheme, netloc, path, query, fragment = urllib.parse.urlsplit(url)
        if not netloc:
            raise ValueError()

        if not path:
            path = '/'
        else:
            path = urllib.parse.unquote(path)

        # check domain idna encoding
        try:
            netloc = netloc.encode('idna').decode('utf-8')
        except UnicodeError:
            raise ValueError('URL has an invalid label.')

        if '@' in netloc:
            authinfo, netloc = netloc.split('@', 1)
            if not auth:
                auth = authinfo.split(':', 1)
                if len(auth) == 1:
                    auth.append('')

        # extract host and port
        ssl = scheme == 'https'

        if ':' in netloc:
            netloc, port_s = netloc.split(':', 1)
            port = int(port_s)
        else:
            if ssl:
                port = http.client.HTTPS_PORT
            else:
                port = http.client.HTTP_PORT

        self.host = netloc
        self.port = port
        self.ssl = ssl

        # build url query
        if isinstance(params, dict):
            params = list(params.items())

        if data and self.method in self.GET_METHODS:
            # include data to query
            if isinstance(data, dict):
                data = data.items()
            params = list(itertools.chain(params or (), data))
            data = None

        if params:
            params = urllib.parse.urlencode(params)
            if query:
                query = '%s&%s' % (query, params)
            else:
                query = params

        # build path
        path = urllib.parse.quote(path)
        self.path = urllib.parse.urlunsplit(('', '', path, query, fragment))

        # headers
        self.headers = email.message.Message()
        if headers:
            if isinstance(headers, dict):
                headers = list(headers.items())

            for key, value in headers:
                self.headers[key] = value

        for hdr, val in self.DEFAULT_HEADERS.items():
            if hdr not in self.headers:
                self.headers[hdr] = val

        # host
        if 'host' not in self.headers:
            self.headers['Host'] = self.host

        # cookies
        if cookies:
            c = http.cookies.SimpleCookie()
            if 'cookie' in self.headers:
                c.load(self.headers.get('cookie', ''))
                del self.headers['cookie']

            for name, value in cookies.items():
                if isinstance(value, http.cookies.Morsel):
                    dict.__setitem__(c, name, value)
                else:
                    c[name] = value

            self.headers['cookie'] = c.output(header='', sep=';').strip()

        # auth
        if auth:
            if isinstance(auth, (tuple, list)) and len(auth) == 2:
                # basic auth
                self.headers['Authorization'] = 'Basic %s' % (
                    base64.b64encode(
                        ('%s:%s' % (auth[0], auth[1])).encode('latin1'))
                    .strip().decode('latin1'))
            else:
                raise ValueError("Only basic auth is supported")

        self._params = (chunked, compress, files, data, encoding)

    def start(self, transport):
        chunked, compress, files, data, encoding = self._params

        request = tulip.http.Request(
            transport, self.method, self.path, self.version)

        # Content-encoding
        enc = self.headers.get('Content-Encoding', '').lower()
        if enc:
            if not chunked:  # enable chunked, no need to deal with length
                chunked = True
            request.add_compression_filter(enc)
        elif compress:
            if not chunked:  # enable chunked, no need to deal with length
                chunked = True
            compress = compress if isinstance(compress, str) else 'deflate'
            self.headers['Content-Encoding'] = compress
            request.add_compression_filter(compress)

        # form data (x-www-form-urlencoded)
        if isinstance(data, dict):
            data = list(data.items())

        if data and not files:
            if not isinstance(data, str):
                data = urllib.parse.urlencode(data, doseq=True)

            self.body = data.encode(encoding)
            if 'content-type' not in self.headers:
                self.headers['content-type'] = (
                    'application/x-www-form-urlencoded')
            if 'content-length' not in self.headers:
                self.headers['content-length'] = len(self.body)

        # files (multipart/form-data)
        elif files:
            fields = []

            if data:
                for field, val in data:
                    fields.append((field, str_to_bytes(val)))

            if isinstance(files, dict):
                files = list(files.items())

            for rec in files:
                if not isinstance(rec, (tuple, list)):
                    rec = (rec,)

                ft = None
                if len(rec) == 1:
                    k = guess_filename(rec[0], 'unknown')
                    fields.append((k, k, rec[0]))

                elif len(rec) == 2:
                    k, fp = rec
                    fn = guess_filename(fp, k)
                    fields.append((k, fn, fp))

                else:
                    k, fp, ft = rec
                    fn = guess_filename(fp, k)
                    fields.append((k, fn, fp, ft))

            chunked = chunked or 8192
            boundary = uuid.uuid4().hex

            self.body = encode_multipart_data(
                fields, bytes(boundary, 'latin1'))

            if 'content-type' not in self.headers:
                self.headers['content-type'] = (
                    'multipart/form-data; boundary=%s' % boundary)

        # chunked
        te = self.headers.get('transfer-encoding', '').lower()

        if chunked:
            self.chunked = True
            if 'content-length' in self.headers:
                del self.headers['content-length']
            if 'chunked' not in te:
                self.headers['Transfer-encoding'] = 'chunked'

            chunk_size = chunked if type(chunked) is int else 8196
            request.add_chunking_filter(chunk_size)
        else:
            if 'chunked' in te:
                self.chunked = True
                request.add_chunking_filter(8196)
            else:
                self.chunked = False
                self.headers['content-length'] = len(self.body)

        request.add_headers(*self.headers.items())
        request.send_headers()

        if isinstance(self.body, (str, bytes)):
            self.body = (self.body,)

        for chunk in self.body:
            request.write(chunk)

        request.write_eof()
        return []


def str_to_bytes(s, encoding='utf-8'):
    if isinstance(s, str):
        return s.encode(encoding)
    return s


def guess_filename(obj, default=None):
    name = getattr(obj, 'name', None)
    if name and name[0] != '<' and name[-1] != '>':
        return os.path.split(name)[-1]
    return default


def encode_multipart_data(fields, boundary, encoding='utf-8', chunk_size=8196):
    """
    Encode a list of fields using the multipart/form-data MIME format.

    fields:
        List of (name, value) or (name, filename, io) or
        (name, filename, io, MIME type) field tuples.
    """
    for rec in fields:
        yield b'--' + boundary + b'\r\n'

        field, *rec = rec

        if len(rec) == 1:
            data = rec[0]
            yield (('Content-Disposition: form-data; name="%s"\r\n\r\n' %
                    (field,)).encode(encoding))
            yield data + b'\r\n'

        else:
            if len(rec) == 3:
                fn, fp, ct = rec
            else:
                fn, fp = rec
                ct = (mimetypes.guess_type(fn)[0] or
                      'application/octet-stream')

            yield ('Content-Disposition: form-data; name="%s"; '
                   'filename="%s"\r\n' % (field, fn)).encode(encoding)
            yield ('Content-Type: %s\r\n\r\n' % (ct,)).encode(encoding)

            if isinstance(fp, str):
                fp = fp.encode(encoding)

            if isinstance(fp, bytes):
                fp = io.BytesIO(fp)

            while True:
                chunk = fp.read(chunk_size)
                if not chunk:
                    break
                yield str_to_bytes(chunk)

            yield b'\r\n'

    yield b'--' + boundary + b'--\r\n'
