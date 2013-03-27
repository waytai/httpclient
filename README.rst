Code here is somewhat outdated. I'm moving code directly tulip repository https://code.google.com/p/tulip/



httpclient library
==================

Simple http client library.

Usage::

      >>> import httpclient
      >>> req = yield from httpclient.request('GET', 'http://python.org/')
      <HttpResponse [200]>


Examples
--------

* crawl.py - simple crawl cmd tool

  >> crawl.py http://python.org


* websocket example, simple websocket server and cmd client

  1. ws server starts on port 8080. start server and open http://localhost:8080 in browser:

      >> wsserver.py

  2. ws client automatically connects to http://localhost:8080

      >> wsclient.py
