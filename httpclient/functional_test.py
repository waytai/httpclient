"""Functional tests"""

import io
import os.path
import unittest
from pprint import pprint

import tulip
from tulip import tasks

from . import api, protocol
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
                            data={'some': 'data'},
                        )))

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

        content = r.read(True)
        self.assertEqual({'some': ['data']}, content['form'])
        self.assertEqual(r.status, 200)

    def test_POST_FILES(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files={'some': f}, chunk_size=1024)))

            content = r.read(True)

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
                            chunk_size=1024, compress='deflate')))

            content = r.read(True)

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

    def test_POST_FILES_LIST(self):
        url = self.server.url('method', 'post')

        with open(__file__) as f:
            r = self.event_loop.run_until_complete(tasks.Task(
                api.request('post', url, files=[('some', f)])))

            content = r.read(True)

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

            content = r.read(True)

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

            content = r.read(True)

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

        content = r.read(True)

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

            content = r.read(True)

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


class HttpClientFunctional(Router):

    @Router.define('/method/([A-Za-z]+)$')
    def method(self, match):
        meth = match.group(1).upper()
        if meth == self._method:
            self._response(200)
        else:
            self._response(400)

    @Router.define('/redirect/([0-9]+)$')
    def redirect(self, match):
        no = int(match.group(1).upper())
        rno = self._server['redirects'] = self._server.get('redirects', 0) + 1

        if rno >= no:
            self._response(
                302, headers={'Location': '/method/%s' % self._method.lower()})
        else:
            self._response(
                302, headers={'Location': self._path})

    @Router.define('/encoding/(gzip|deflate)$')
    def encoding(self, match):
        mode = match.group(1)

        self._response(
            200,
            headers={'Content-encoding': mode},
            writers=[protocol.DeflateWriter(mode)])
