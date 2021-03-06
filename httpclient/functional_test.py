"""Functional tests"""

import io
import os.path
import unittest

import tulip
import tulip.http
from tulip import tasks

from . import api, protocol, utils
from .test_utils import Router, HttpServer


class FunctionalTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = tulip.new_event_loop()
        tulip.set_event_loop(self.event_loop)

        self.server = HttpServer(HttpClientFunctional, self.event_loop)
        self.server.start()

    def tearDown(self):
        self.event_loop.close()

    def test_HTTP_200_OK_METHOD(self):
        for meth in ('get', 'post', 'put', 'delete'):
            r = self.event_loop.run_until_complete(
                tasks.Task(
                    api.request(meth, self.server.url('method', meth))))
            content = r.content.decode()

            self.assertEqual(r.status, 200)
            self.assertIn('"method": "%s"' % meth.upper(), content)

    def test_HTTP_302_REDIRECT_GET(self):
        r = self.event_loop.run_until_complete(
            tasks.Task(
                api.request('get', self.server.url('redirect', 2))))

        self.assertEqual(r.status, 200)
        self.assertEqual(2, self.server.get('redirects'))

    def test_HTTP_302_REDIRECT_POST(self):
        r = self.event_loop.run_until_complete(
            tasks.Task(
                api.request('post', self.server.url('redirect', 2),
                            data={'some': 'data'})))

        content = r.content.decode()

        self.assertEqual(r.status, 200)
        self.assertIn('"method": "POST"', content)
        self.assertEqual(2, self.server.get('redirects'))

    def test_HTTP_302_max_redirects(self):
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('redirect', 5),
                        max_redirects=2)))

        self.assertEqual(r.status, 302)
        self.assertEqual(2, self.server.get('redirects'))

    def test_HTTP_200_GET_WITH_PARAMS(self):
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('method', 'get'),
                        params={'q': 'test'})))
        content = r.content.decode()

        self.assertIn('"query": "q=test"', content)
        self.assertEqual(r.status, 200)

    def test_HTTP_200_GET_WITH_MIXED_PARAMS(self):
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('method', 'get') + '?test=true',
                        params={'q': 'test'})))
        content = r.content.decode()

        self.assertIn('"query": "test=true&q=test"', content)
        self.assertEqual(r.status, 200)

    def test_POST_DATA(self):
        url = self.server.url('method', 'post')
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('post', url, data={'some': 'data'})))
        self.assertEqual(r.status, 200)

        content = self.event_loop.run_until_complete(tasks.Task(r.read(True)))
        self.assertEqual({'some': ['data']}, content['form'])
        self.assertEqual(r.status, 200)

    def test_POST_DATA_DEFLATE(self):
        url = self.server.url('method', 'post')
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('post', url, data={'some': 'data'}, compress=True)))
        self.assertEqual(r.status, 200)

        content = self.event_loop.run_until_complete(tasks.Task(r.read(True)))
        self.assertEqual('deflate', content['compression'])
        self.assertEqual({'some': ['data']}, content['form'])
        self.assertEqual(r.status, 200)

    def test_POST_FILES(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files={'some': f}, chunked=1024)))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            filename = os.path.split(f.name)[-1]

            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                'some', content['multipart-data'][0]['name'])
            self.assertEqual(
                filename, content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_DEFLATE(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files={'some': f},
                            chunked=1024, compress='deflate')))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            filename = os.path.split(f.name)[-1]

            self.assertEqual('deflate', content['compression'])
            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                'some', content['multipart-data'][0]['name'])
            self.assertEqual(
                filename, content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_STR(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files=[('some', f.read())])))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                'some', content['multipart-data'][0]['name'])
            self.assertEqual(
                'some', content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_LIST(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files=[('some', f)])))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            filename = os.path.split(f.name)[-1]

            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                'some', content['multipart-data'][0]['name'])
            self.assertEqual(
                filename, content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_LIST_CT(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files=[('some', f, 'text/plain')])))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            filename = os.path.split(f.name)[-1]

            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                'some', content['multipart-data'][0]['name'])
            self.assertEqual(
                filename, content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(
                'text/plain', content['multipart-data'][0]['content-type'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_SINGLE(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files=[f])))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            f.seek(0)
            filename = os.path.split(f.name)[-1]

            self.assertEqual(1, len(content['multipart-data']))
            self.assertEqual(
                filename, content['multipart-data'][0]['name'])
            self.assertEqual(
                filename, content['multipart-data'][0]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][0]['data'])
            self.assertEqual(r.status, 200)

    def test_POST_FILES_IO(self):
        url = self.server.url('method', 'post')

        data = io.BytesIO(b'data')

        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('post', url, files=[data])))

        content = self.event_loop.run_until_complete(
            tasks.Task(r.read(True)))

        self.assertEqual(1, len(content['multipart-data']))
        self.assertEqual(
            {'content-type': 'application/octet-stream',
             'data': 'data',
             'filename': 'unknown',
             'name': 'unknown'}, content['multipart-data'][0])
        self.assertEqual(r.status, 200)

    def test_POST_FILES_WITH_DATA(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url,
                            data={'test': 'true'}, files={'some': f})))

            content = self.event_loop.run_until_complete(
                tasks.Task(r.read(True)))

            self.assertEqual(2, len(content['multipart-data']))
            self.assertEqual(
                'test', content['multipart-data'][0]['name'])
            self.assertEqual(
                'true', content['multipart-data'][0]['data'])

            f.seek(0)
            filename = os.path.split(f.name)[-1]
            self.assertEqual(
                'some', content['multipart-data'][1]['name'])
            self.assertEqual(
                filename, content['multipart-data'][1]['filename'])
            self.assertEqual(
                f.read(), content['multipart-data'][1]['data'])
            self.assertEqual(r.status, 200)

    def test_encoding(self):
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('encoding', 'deflate'))))
        self.assertEqual(r.status, 200)

        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('encoding', 'gzip'))))
        self.assertEqual(r.status, 200)

    def test_chunked(self):
        r = self.event_loop.run_until_complete(tasks.Task(
            api.request('get', self.server.url('chunked'))))
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers['Transfer-Encoding'], 'chunked')
        content = self.event_loop.run_until_complete(tasks.Task(r.read(True)))
        self.assertEqual(content['path'], '/chunked')

    def _test_timeout(self):
        self.server.noresponse = True
        self.assertRaises(
            tulip.futures.CancelledError,
            self.event_loop.run_until_complete,
            api.request('get', self.server.url('method', 'get'),
                        timeout=0.1))

    def test_request_conn_error(self):
        self.assertRaises(
            ConnectionRefusedError,
            self.event_loop.run_until_complete,
            tasks.Task(
                api.request('get', 'http://0.0.0.0:9989', timeout=0.1)))

    def test_stream(self):
        wstream, response_fut = self.event_loop.run_until_complete(
            tasks.Task(
                api.stream('get', self.server.url('method', 'get'))))

        r = self.event_loop.run_until_complete(
            tasks.Task(response_fut))

        content = self.event_loop.run_until_complete(tasks.Task(r.read()))
        content = content.decode()

        self.assertEqual(r.status, 200)
        self.assertIn('"method": "GET"', content)

    def _test_stream_conn_error(self):
        self.assertRaises(
            ValueError,
            self.event_loop.run_until_complete,
            tasks.Task(api.stream('get', 'http://0.0.0.0:78989', timeout=0.1)))


class HttpClientFunctional(Router):

    @Router.define('/method/([A-Za-z]+)$')
    def method(self, match):
        meth = match.group(1).upper()
        if meth == self._method:
            self._response(self._start_response(200))
        else:
            self._response(self._start_response(400))

    @Router.define('/redirect/([0-9]+)$')
    def redirect(self, match):
        no = int(match.group(1).upper())
        rno = self._server['redirects'] = self._server.get('redirects', 0) + 1

        if rno >= no:
            self._response(
                self._start_response(302),
                headers={'Location': '/method/%s' % self._method.lower()})
        else:
            self._response(
                self._start_response(302),
                headers={'Location': self._path})

    @Router.define('/encoding/(gzip|deflate)$')
    def encoding(self, match):
        mode = match.group(1)

        resp = self._start_response(200)
        resp.add_compression_filter(mode)
        resp.add_chunking_filter(100)
        self._response(resp, headers={'Content-encoding': mode}, chunked=True)

    @Router.define('/chunked$')
    def chunked(self, match):
        resp = self._start_response(200)
        resp.add_chunking_filter(100)
        self._response(resp, chunked=True)
