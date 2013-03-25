"""public api"""

__all__ = ['stream', 'request']

import urllib.parse
from tulip import events
from tulip import futures
from tulip import tasks

from .request import HttpRequest
from .response import HttpResponse
from .protocol import HttpProtocol


@tasks.coroutine
def request(method, url, *,
            params=None, data=None, headers=None, cookies=None,
            files=None, auth=None, allow_redirects=True, max_redirects=25,
            encoding='utf-8', version='1.1', timeout=None,
            compress=None, chunked=None):
    """Constructs and sends a request. Returns response object

    method: http method
    url: URL for
    params: (optional) Dictionary or bytes to be sent in the query string
      of the new request
    data: (optional) Dictionary, bytes, or file-like object to
      send in the body of the request
    headers: (optional) Dictionary of HTTP Headers to send with the request
    cookies: (optional) Dict object to send with the request
    files: (optional) Dictionary of 'name': file-like-objects
       for multipart encoding upload
    auth: (optional) Auth tuple to enable Basic HTTP Auth
    timeout: (optional) Float describing the timeout of the request
    allow_redirects: (optional) Boolean. Set to True if POST/PUT/DELETE
       redirect following is allowed.
    compress: Boolean. Set to True if request has to be compressed
       with deflate encoding
    chunked: Boolean or Integer. Set to chunk size for chunked
       transfer encoding

    Usage:

      >>> import httpclient
      >>> req = yield from httpclient.request('GET', 'http://python.org/')
      >>> req
      <HttpResponse [200]>

    """
    event_loop = events.get_event_loop()

    redirects = 0

    while True:
        request = HttpRequest(
            method, url, params=params, headers=headers, data=data,
            cookies=cookies, files=files, auth=auth, encoding=encoding,
            version=version, compress=compress, chunked=chunked)
        response = HttpResponse(request.method, request.path)

        conn = event_loop.create_connection(
            HttpProtocol, request.host, request.port, ssl=request.ssl)

        # connection timeout
        try:
            transport, protocol = yield from tasks.Task(
                start(conn, request, response), timeout=timeout)
        except futures.CancelledError:
            raise futures.TimeoutError

        # redirects
        if response.status in (301, 302) and allow_redirects:
            redirects += 1
            if max_redirects and redirects >= max_redirects:
                break

            r_url = (response.headers.get('location') or
                     response.headers.get('uri'))
            if r_url[:7] not in ('http://', 'https:/'):
                scheme, netloc = urllib.parse.urlsplit(url)[:2]
                url = urllib.parse.urlunsplit(
                    (scheme, netloc, r_url, '', ''))
            else:
                url = r_url

            if url:
                response.close()
                continue

        break

    return response


@tasks.coroutine
def start(conn, request, response):
    transport, protocol = yield from conn

    try:
        yield from request.start(transport)
        yield from response.start(protocol.stream, transport, True)
    except:
        import traceback
        traceback.print_exc()

    return transport, protocol


@tasks.coroutine
def stream(method, url, *,
           params=None, headers=None, cookies=None,
           auth=None, encoding='utf-8', version='1.1', timeout=None):
    """Constructs a request, sends request headers.
    Returns write stream and response coroutine.

    """
    event_loop = events.get_event_loop()

    request = HttpRequest(
        method, url, params=params, headers=headers,
        cookies=cookies, auth=auth, encoding=encoding, version=version)
    response = HttpResponse(request.method, request.path)

    conn = event_loop.create_connection(
        HttpProtocol, request.host, request.port, ssl=request.ssl)

    try:
        transport, protocol = yield from tasks.Task(conn, timeout=timeout)
    except futures.CancelledError:
        raise futures.TimeoutError

    request.start(transport)
    return protocol.stream, response.start(protocol.stream, transport)
