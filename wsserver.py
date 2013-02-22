""" websocket server """
import signal
import email.message
import email.parser
import os
import re
from pprint import pprint

import tulip
from httpclient import HttpStreamReader

from wsproto import WebSocketProto


class HttpServer(tulip.Protocol):

    _connections = []

    def __init__(self):
        super().__init__()
        self.transport = None
        self.reader = None
        self.handler = None

    @tulip.task
    def handle_request(self):
        bmethod, bpath, bversion = yield from self.reader.read_request_status()
        print('method = {!r}; path = {!r}; version = {!r}'.format(
            bmethod, bpath, bversion))

        headers = yield from self.reader.read_headers()
        print(headers)

        if 'websocket' in headers.get('UPGRADE', '').lower():
            # init ws
            wsclient = WebSocketProto()
            status, headers = wsclient.serve(
                headers, self.transport, self.reader)

            write = self.transport.write
            write(b'HTTP/1.1 ' + status.encode())
            for hdr in headers:
                write(hdr)
            write(b'\r\n')

            if status.startswith('101'):
                # start websocket

                @tulip.coroutine
                def rstream():
                    while True:
                        try:
                            data = yield from wsclient.receive()
                            if not data:
                                break
                        except:
                            break

                        data = data.strip()
                        for wsc in self._connections:
                            if wsc is not wsclient:
                                wsc.send(data.encode())

                for wsc in self._connections:
                    wsc.send(b'Someone joined.')

                self._connections.append(wsclient)
                t = tulip.Task(rstream())
                done, pending = yield from tulip.wait([t])
                assert t in done
                assert not pending
                self._connections.remove(wsclient)

                for wsc in self._connections:
                    wsc.send(b'Someone disconnected.')
        else:
            write = self.transport.write
            write(b'HTTP/1.0 200 Ok\r\n')
            write(b'Content-type: text/html\r\n')
            write(b'\r\n')
            write(WS_SRV_HTML)

        self.transport.close()

    def connection_made(self, transport):
        self.transport = transport
        print('connection made', transport, transport.get_extra_info('socket'))
        self.reader = HttpStreamReader(transport)
        self.handler = self.handle_request()

    def data_received(self, data):
        self.reader.feed_data(data)

    def eof_received(self):
        self.reader.feed_eof()

    def connection_lost(self, exc):
        print('connection lost', exc)
        if (self.handler.done() and
                not self.handler.cancelled() and
                self.handler.exception() is not None):
            print('handler exception:', self.handler.exception())


def main():
    loop = tulip.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    f = loop.start_serving(HttpServer, '127.0.0.1', 8080)
    x = loop.run_until_complete(f)
    print('serving on', x.getsockname())
    loop.run_forever()


WS_SRV_HTML = b"""
<!DOCTYPE html>
<meta charset="utf-8" />
<html>
<head>
  <script src="http://ajax.googleapis.com/ajax/libs/jquery/1.4.2/jquery.min.js"></script>
  <script language="javascript" type="text/javascript">
    $(function() {
      var conn = null;
      function log(msg) {
        var control = $('#log');
        control.html(control.html() + msg + '<br/>');
        control.scrollTop(control.scrollTop() + 1000);
      }
      function connect() {
        disconnect();
        var wsUri = "ws://localhost:8080/";
        conn = new WebSocket(wsUri);
        log('Connecting...');
        conn.onopen = function() {
          log('Connected.');
          update_ui();
        };
        conn.onmessage = function(e) {
          log('Received: ' + e.data);
        };
        conn.onclose = function() {
          log('Disconnected.');
          conn = null;
          update_ui();
        };
      }
      function disconnect() {
        if (conn != null) {
          log('Disconnecting...');
          conn.close();
          conn = null;
          update_ui();
        }
      }
      function update_ui() {
        var msg = '';
        if (conn == null) {
          $('#status').text('disconnected');
          $('#connect').html('Connect');
        } else {
          $('#status').text('connected (' + conn.protocol + ')');
          $('#connect').html('Disconnect');
        }
      }
      $('#connect').click(function() {
        if (conn == null) {
          connect();
        } else {
          disconnect();
        }
        update_ui();
        return false;
      });
      $('#send').click(function() {
        var text = $('#text').val();
        log('Sending: ' + text);
        conn.send(text);
        $('#text').val('').focus();
        return false;
      });
      $('#text').keyup(function(e) {
        if (e.keyCode === 13) {
          $('#send').click();
          return false;
        }
      });
    });
</script>
</head>
<body>
<h3>Chat!</h3>
<div>
  <button id="connect">Connect</button>&nbsp;|&nbsp;Status: <span id="status">disconnected</span>
</div>
<div id="log" style="width: 20em; height: 15em; overflow:auto; border: 1px solid black">
</div>
<form id="chatform" onsubmit="return false;">
  <input id="text" type="text" />
  <input id="send" type="button" value="Send" />
</form>
</body>
</html>
"""


if __name__ == '__main__':
    main()
