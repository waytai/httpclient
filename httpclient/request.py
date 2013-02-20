"""http request"""

import base64
import email.message
import http.client
import http.cookies
import io
import itertools
import mimetypes
import uuid
import urllib.parse


def encode_multipart_formdata(fields, encoding='utf-8'):
    """
    Encode a list of fields using the multipart/form-data MIME format.

    fields:
        List of (name, value) or (name, key, value) or
        (name, key, value, MIME type) field tuples.
    """
    body = io.BytesIO()
    boundary = uuid.uuid4().hex

    for rec in fields:
        body.write(b'--' + boundary + b'--\r\n')

        field, rec = rec[0], rec[1:]

        if isinstance(rec, str):
            data = rec
            body.write(
                ('Content-Disposition: form-data; name="%s"\r\n\r\n' % (
                    field,).encode(encoding)))
        else:
            if len(rec) == 3:
                filename, data, content_type = rec
            else:
                filename, data = rec
                content_type = (mimetypes.guess_type(filename)[0] or
                                'application/octet-stream')
            body.write(
                ('Content-Disposition: form-data; name="%s"; '
                 'filename="%s"\r\n' % (field, filename)).encode(encoding))
            body.write(
                ('Content-Type: %s\r\n\r\n' % (content_type,)).encode(encoding))

        body.write(data.encode('utf-8'))
        body.write(b'\r\n')

    body.write(b'--' + boundary + b'--\r\n')
    return body.getvalue(), 'multipart/form-data; boundary=%s' % boundary


class HttpRequest:

    GET_METHODS = {'DELETE', 'GET', 'HEAD', 'OPTIONS'}
    POST_METHODS = {'PATCH', 'POST', 'PUT', 'TRACE'}
    ALL_METHODS = GET_METHODS.union(POST_METHODS)

    body = b''

    def __init__(self, method, url, *,
                 params=None, headers=None, data=None, cookies=None,
                 files=None, auth=None, encoding='utf-8', version='1.1'):
        self.method = method.upper()
        self.version = version
        self.encoding = encoding

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

        # data
        if isinstance(data, dict):
            data = list(data.items())

        if data and not files:
            if not isinstance(data, str):
                data = urllib.parse.urlencode(data, doseq=True)

            self.body = data
            if 'content-type' not in self.headers:
                self.headers['content-type'] = (
                    'application/x-www-form-urlencoded')
            if 'content-length' not in self.headers:
                self.headers['content-length'] = len(self.body)

        elif files:
            fields = []

            if data:
                for field, val in data:
                    fields.append((field, str(val)))

            if isinstance(files, dict):
                files = list(files.items())

            def guess_filename(obj, default=None):
                name = getattr(obj, 'name', None)
                if name and name[0] != '<' and name[-1] != '>':
                    return os.path.split(name)[-1]
                return default

            for rec in files:
                ft = None
                if len(rec) == 1:
                    k = fn = guess_filename(rec, 'unknown')
                    fp = rec
                elif len(rec) == 2:
                    k, fp = rec
                    fn = guess_filename(fp, k)
                else:
                    k, fp, ft = rec
                    fn = guess_filename(fp, k)

                if isinstance(fp, str):
                    fp = io.StringIO(fp)
                if isinstance(fp, bytes):
                    fp = io.BytesIO(fp)

                if ft:
                    new_v = (k, fn, fp.read(), ft)
                else:
                    new_v = (k, fn, fp.read())
                fields.append(new_v)

            self.body, content_type = encode_multipart_formdata(fields)
            self.headers['content-length'] = len(self.body)
            if 'content-type' not in self.headers:
                self.headers['content-type'] = content_type

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

    def begin(self, wstream):
        line = '{} {} HTTP/{}\r\n'.format(self.method, self.path, self.version)
        wstream.write_str(line)

        for key, value in self.headers.items():
            wstream.write_str('{}: {}\r\n'.format(key, value))
        wstream.write(b'\r\n')

        if self.body:
            if isinstance(self.body, bytes):
                wstream.write(self.body)
            else:
                wstream.write(self.body.encode(self.encoding))
