""" websocket server """
import signal
import email.message
import email.parser
import http.client
import os
import re
from pprint import pprint

import tulip
from httpclient import ServerHttpProtocol

from wsproto import WebSocketProto


class HttpServer(ServerHttpProtocol):

    _connections = []

    @tulip.coroutine
    def handle_one_request(self, rline, message):
        self.close()

        # headers
        headers = http.client.HTTPMessage()
        for hdr, val in message.headers:
            headers[hdr] = val

        if 'websocket' in headers.get('UPGRADE', '').lower():
            # init ws
            wsclient = WebSocketProto()
            status, headers = wsclient.serve(
                headers, self.transport, self.rstream)

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
                        print(data)
                        for wsc in self._connections:
                            if wsc is not wsclient:
                                wsc.send(data.encode())

                print('Someone joined.')
                for wsc in self._connections:
                    wsc.send(b'Someone joined.')

                self._connections.append(wsclient)
                t = tulip.Task(rstream())
                done, pending = yield from tulip.wait([t])
                assert t in done
                assert not pending
                self._connections.remove(wsclient)

                print('Someone disconnected.')
                for wsc in self._connections:
                    wsc.send(b'Someone disconnected.')
        else:
            write = self.transport.write
            write(b'HTTP/1.0 200 Ok\r\n')
            write(b'Content-type: text/html\r\n')
            write(b'\r\n')
            write(WS_SRV_HTML)


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
