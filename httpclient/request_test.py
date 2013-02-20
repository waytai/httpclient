# -*- coding: utf-8 -*-
"""Tests for request.py"""

import unittest
import urllib.parse

from .request import HttpRequest


class HttpRequestTests(unittest.TestCase):

    def test_method(self):
        req = HttpRequest('get', 'http://python.org/')
        self.assertEqual(req.method, 'GET')

        req = HttpRequest('head', 'http://python.org/')
        self.assertEqual(req.method, 'HEAD')

        req = HttpRequest('HEAD', 'http://python.org/')
        self.assertEqual(req.method, 'HEAD')

    def test_host_port(self):
        req = HttpRequest('get', 'http://python.org/')
        self.assertEqual(req.host, 'python.org')
        self.assertEqual(req.port, 80)
        self.assertFalse(req.ssl)

        req = HttpRequest('get', 'https://python.org/')
        self.assertEqual(req.host, 'python.org')
        self.assertEqual(req.port, 443)
        self.assertTrue(req.ssl)

        req = HttpRequest('get', 'https://python.org:960/')
        self.assertEqual(req.host, 'python.org')
        self.assertEqual(req.port, 960)
        self.assertTrue(req.ssl)

    def test_host_header(self):
        req = HttpRequest('get', 'http://python.org/')
        self.assertEqual(req.headers['host'], 'python.org')

        req = HttpRequest('get', 'http://python.org/',
                          headers={'host': 'example.com'})
        self.assertEqual(req.headers['host'], 'example.com')

    def test_invalid_url(self):
        self.assertRaises(ValueError, HttpRequest, 'get', 'hiwpefhipowhefopw')

    def test_no_path(self):
        req = HttpRequest('get', 'http://python.org')
        self.assertEqual('/', req.path)

    def test_basic_auth(self):
        req = HttpRequest('get', 'http://python.org', auth=('nkim', '1234'))
        self.assertIn('Authorization', req.headers)
        self.assertEqual('Basic bmtpbToxMjM0', req.headers['Authorization'])

    def test_basic_auth_from_url(self):
        req = HttpRequest('get', 'http://nkim:1234@python.org')
        self.assertIn('Authorization', req.headers)
        self.assertEqual('Basic bmtpbToxMjM0', req.headers['Authorization'])

        req = HttpRequest('get', 'http://nkim@python.org')
        self.assertIn('Authorization', req.headers)
        self.assertEqual('Basic bmtpbTo=', req.headers['Authorization'])

    def test_no_content_length(self):
        req = HttpRequest('get', 'http://python.org')
        self.assertNotIn('Content-Length', req.headers)

        req = HttpRequest('head', 'http://python.org')
        self.assertNotIn('Content-Length', req.headers)

    def test_path_is_not_double_encoded(self):
        req = HttpRequest('get', "http://0.0.0.0/get/test case")
        self.assertEqual(req.path, "/get/test%20case")

        req = HttpRequest('get', "http://0.0.0.0/get/test%20case")
        self.assertEqual(req.path, "/get/test%20case")

    def test_params_are_added_before_fragment(self):
        req = HttpRequest(
            'GET', "http://example.com/path#fragment", params={"a": "b"})
        self.assertEqual(
            req.path, "/path?a=b#fragment")

        req = HttpRequest(
            'GET',
            "http://example.com/path?key=value#fragment", params={"a": "b"})
        self.assertEqual(
            req.path, "/path?key=value&a=b#fragment")

    def test_cookies(self):
        req = HttpRequest(
            'get', 'http://test.com/path', cookies={'cookie1': 'val1'})
        self.assertIn('Cookie', req.headers)
        self.assertEqual('cookie1=val1', req.headers['cookie'])

        req = HttpRequest(
            'get', 'http://test.com/path',
            headers={'cookie': 'cookie1=val1'},
            cookies={'cookie2': 'val2'})
        self.assertEqual('cookie1=val1; cookie2=val2', req.headers['cookie'])

    def test_unicode_get(self):
        def join(*suffix):
            return urllib.parse.urljoin('http://python.org/', '/'.join(suffix))

        url = 'http://python.org'
        req = HttpRequest('get', url, params={'foo': 'føø'})
        self.assertEqual('/?foo=f%C3%B8%C3%B8', req.path)
        req = HttpRequest('', url, params={'føø': 'føø'})
        self.assertEqual('/?f%C3%B8%C3%B8=f%C3%B8%C3%B8', req.path)
        req = HttpRequest('', url, params={'foo': 'foo'})
        self.assertEqual('/?foo=foo', req.path)
        req = HttpRequest('', join('ø'), params={'foo': 'foo'})
        self.assertEqual('/%C3%B8?foo=foo', req.path)

    def test_query_multivalued_param(self):
        for meth in HttpRequest.ALL_METHODS:
            req = HttpRequest(
                meth, 'http://python.org',
                params=(('test', 'foo'), ('test', 'baz')))
            self.assertEqual(req.path, '/?test=foo&test=baz')

    def test_post_data(self):
        for meth in HttpRequest.POST_METHODS:
            req = HttpRequest(meth, 'http://python.org/', data={'life': '42'})
            self.assertEqual(
                '/', req.path)
            self.assertEqual(
                'life=42', req.body)
            self.assertEqual(
                'application/x-www-form-urlencoded',
                req.headers['content-type'])

    def test_get_with_data(self):
        for meth in HttpRequest.GET_METHODS:
            req = HttpRequest(meth, 'http://python.org/', data={'life': '42'})
            self.assertEqual('/?life=42', req.path)


if __name__ == '__main__':
    unittest.main()
