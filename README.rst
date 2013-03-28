Most of code has been incorporated into main tulip repo, i'm going to remove this repo simetime next week.
https://code.google.com/p/tulip/



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
