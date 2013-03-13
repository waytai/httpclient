""" websocket client """
import logging
import signal
import sys

import tulip

from wsproto import WebSocketProto


@tulip.coroutine
def rstream(wsclient):
    while True:
        try:
            data = yield from wsclient.receive()
            if not data:
                break
        except:
            break

        print(data.strip())


@tulip.coroutine
def wstream(name, wsclient, stream):
    name = name + b': '

    while not stream.eof:
        line = name + (yield from stream.readline())
        print(line.decode().strip())
        wsclient.send(line)


@tulip.task
def chat(name, url, wsclient):
    yield from wsclient.connect(url)
    print('Connected.')

    # stdin reader
    stream = tulip.StreamReader()

    def cb():
        stream.feed_data(sys.stdin.readline().encode())

    event_loop = tulip.get_event_loop()
    event_loop.add_reader(sys.stdin.fileno(), cb)

    yield from tulip.wait(
        [rstream(wsclient), wstream(name, wsclient, stream)],
        return_when=tulip.FIRST_COMPLETED)


def main():
    name = input('Please enter your name: ').encode()

    url = 'http://localhost:8080'
    wsclient = WebSocketProto()

    loop = tulip.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, loop.stop)
    except RuntimeError:
        pass
    try:
        loop.run_until_complete(chat(name, url, wsclient))
    except:
        pass


if __name__ == '__main__':
    if '--iocp' in sys.argv:
        from tulip import events, windows_events
        sys.argv.remove('--iocp')
        logging.info('using iocp')
        el = windows_events.ProactorEventLoop()
        events.set_event_loop(el)
    main()
