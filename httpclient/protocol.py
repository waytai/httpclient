
__all__ = ['HttpProtocol']

import tulip


class HttpProtocol(tulip.Protocol):

    stream = None
    transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.stream = tulip.http.HttpStreamReader()

    def data_received(self, data):
        self.stream.feed_data(data)

    def eof_received(self):
        self.stream.feed_eof()

    def connection_lost(self, exc):
        pass
