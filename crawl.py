#!/usr/bin/env python3

import logging
import re
import signal
import socket
import urllib.parse

import tulip
import httpclient

END = '\n'
MAXTASKS = 100


class Crawler:

    def __init__(self, rooturl):
        self.rooturl = rooturl
        self.busy = set()
        self.done = {}
        self.tasks = set()
        self.sem = tulip.Semaphore(MAXTASKS)
        self.addurls(((rooturl, ''),))  # Set initial work.

    @tulip.task
    def addurls(self, urls):
        for url, parenturl in urls:
            url = urllib.parse.urljoin(parenturl, url)
            url, frag = urllib.parse.urldefrag(url)
            if (url.startswith(self.rooturl) and
                url not in self.busy and url not in self.done):
                yield from self.sem.acquire()
                task = self.process(url)
                task.add_done_callback(lambda t: self.sem.release())
                task.add_done_callback(self.tasks.remove)
                self.tasks.add(task)

    @tulip.task
    def run(self):
        yield from tulip.sleep(1)

        while self.busy:
            yield from tulip.sleep(0.3)
            print(len(self.done), 'completed tasks,', len(self.tasks),
                  'still pending   ', end=END)

        tulip.get_event_loop().stop()

    @tulip.task
    def process(self, url):
        ok = False
        response = None
        extracted = set()
        self.busy.add(url)

        try:
            print('processing:', url, end=END)

            delay = 1
            while True:
                try:
                    response = yield from httpclient.request('get', url)
                    break
                except Exception as exc:
                    if delay >= 60:
                        raise
                    print('...', url, 'has error', repr(str(exc)),
                          'retrying after sleep', delay, '...', end=END)
                    yield from tulip.sleep(delay)
                    delay *= 2

            if response.status == 200:
                ctype = response.headers.get_content_type()
                if ctype == 'text/html':
                    data = response.content.decode('utf-8', 'replace')
                    urls = re.findall(r'(?i)href=["\']?([^\s"\'<>]+)', data)
                    self.addurls([(u, url) for u in urls])

            ok = True
        finally:
            if response is not None:
                response.close()

            self.done[url] = ok
            if url in self.busy:
                self.busy.remove(url)


def main():
    rooturl = sys.argv[1]
    c = Crawler(rooturl)
    c.run()

    loop = tulip.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, loop.stop)
    except RuntimeError:
        pass
    loop.run_forever()
    print('busy:', len(c.busy))
    print('done:', len(c.done), '; ok:', sum(c.done.values()))
    print('tasks:', len(c.tasks))


if __name__ == '__main__':
    if '--iocp' in sys.argv:
        from tulip import events, windows_events
        sys.argv.remove('--iocp')
        logging.info('using iocp')
        el = windows_events.ProactorEventLoop()
        events.set_event_loop(el)
    main()
