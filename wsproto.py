import base64
import hashlib
import os
import struct

import tulip
import httpclient

WS_KEY = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

BAD_REQUEST = ('400 Bad Request\r\n',
               [(b'Connection: close\r\n'), (b'Content-Length: 0\r\n')])


class WebSocketError(Exception):
    pass


class WebSocketProto:

    OPCODE_TEXT = 0x1
    OPCODE_BINARY = 0x2
    OPCODE_CLOSE = 0x8
    OPCODE_PING = 0x9
    OPCODE_PONG = 0xA

    _rstream = None
    _wstream = None

    def __init__(self):
        self.close_code = None
        self.close_message = None
        self._reading = False
        self._closed = False
        self._chunks = bytearray()

    def serve(self, environ, wstream, rstream):
        self._wstream = wstream
        self._rstream = rstream

        if 'websocket' not in environ.get('UPGRADE', '').lower():
            return BAD_REQUEST

        if 'upgrade' not in environ.get('CONNECTION', '').lower():
            return BAD_REQUEST

        version = environ.get("SEC-WEBSOCKET-VERSION")
        if not version or version not in ['13', '8']:
            return BAD_REQUEST

        # check client handshake for validity
        key = environ.get("SEC-WEBSOCKET-KEY")
        if not key or len(base64.b64decode(key)) != 16:
            return BAD_REQUEST

        # prepare response
        return ('101 Switching Protocols\r\n',
                [(b"Upgrade: websocket\r\n"),
                 (b"Connection: Upgrade\r\n"),
                 (b"Transfer-Encoding: chunked\r\n"),
                 (b"Sec-WebSocket-Accept: " + base64.b64encode(
                     hashlib.sha1(key.encode() + WS_KEY).digest()) +
                  b'\r\n')])

    @tulip.coroutine
    def connect(self, url):
        self.url = url
        self.sec_key = base64.b64encode(os.urandom(16))

        self._wstream, fut = yield from httpclient.stream(
            'get', self.url,
            headers={
                'UPGRADE': 'WebSocket',
                'CONNECTION': 'Upgrade',
                'SEC-WEBSOCKET-VERSION': '13',
                'SEC-WEBSOCKET-KEY': self.sec_key.decode(),
            },
            timeout=1.0
        )

        response = yield from fut
        headers = response.headers

        if response.status != 101:
            raise ValueError("Handshake error: Invalid response status")

        if headers.get('upgrade', '').lower() != 'websocket':
            raise ValueError("Handshake error - Invalid upgrade header")

        if headers.get('connection', '').lower() != 'upgrade':
            raise ValueError("Handshake error - Invalid connection header")

        key = headers.get('sec-websocket-accept', '').encode()
        match = base64.b64encode(
            hashlib.sha1(self.sec_key + WS_KEY).digest())
        if key != match:
            raise ValueError("Handshake error - Invalid challenge response")

        self._rstream = response.stream
        self._response = response

    def _parse_header(self, data):
        if len(data) != 2:
            raise WebSocketError(
                'Incomplete read while reading header: %r' % data)

        first_byte, second_byte = struct.unpack('!BB', data)

        fin = (first_byte >> 7) & 1
        rsv1 = (first_byte >> 6) & 1
        rsv2 = (first_byte >> 5) & 1
        rsv3 = (first_byte >> 4) & 1
        opcode = first_byte & 0xf

        # frame-fin = %x0 ; more frames of this message follow
        #           / %x1 ; final frame of this message

        # frame-rsv1 = %x0 ; 1 bit, MUST be 0 unless negotiated otherwise
        # frame-rsv2 = %x0 ; 1 bit, MUST be 0 unless negotiated otherwise
        # frame-rsv3 = %x0 ; 1 bit, MUST be 0 unless negotiated otherwise
        if rsv1 or rsv2 or rsv3:
            self.close(1002)
            raise WebSocketError(
                'Received frame with non-zero reserved bits: %r' % str(data))

        if opcode > 0x7 and fin == 0:
            self.close(1002)
            raise WebSocketError(
                'Received fragmented control frame: %r' % str(data))

        if len(self._chunks) > 0 and fin == 0 and not opcode:
            self.close(1002)
            raise WebSocketError(
                'Received new fragment frame with non-zero opcode: %r' %
                str(data))

        if (len(self._chunks) > 0 and fin == 1 and
                (self.OPCODE_TEXT <= opcode <= self.OPCODE_BINARY)):
            self.close(1002)
            raise WebSocketError(
                'Received new unfragmented data frame during '
                'fragmented message: %r' % str(data))

        has_mask = (second_byte >> 7) & 1
        length = (second_byte) & 0x7f

        # Control frames MUST have a payload length of 125 bytes or less
        if opcode > 0x7 and length > 125:
            self.close(1002)
            raise FrameTooLargeException(
                "Control frame payload cannot be larger than 125 "
                "bytes: %r" % str(data))

        return fin, opcode, has_mask, length

    def _receive_frame(self):
        """Return the next frame from the socket."""
        stream = self._rstream

        data0 = yield from stream.read(2)
        if not data0:
            return

        fin, opcode, has_mask, length = self._parse_header(data0)

        if not has_mask:
            mask = None

        if length < 126:
            data1 = b''
        elif length == 126:
            data1 = yield from stream.read(2)

            if len(data1) != 2:
                self.close()
                raise WebSocketError(
                    'Incomplete read while reading 2-byte length: %r' % (
                        data0 + data1))

            length = struct.unpack('!H', data1)[0]
        else:
            data1 = yield from stream.read(8)

            if len(data1) != 8:
                self.close()
                raise WebSocketError(
                    'Incomplete read while reading 8-byte length: %r' % (
                        data0 + data1))

            length = struct.unpack('!Q', data1)[0]

        if has_mask:
            mask = yield from stream.read(4)
            if len(mask) != 4:
                raise WebSocketError(
                    'Incomplete read while reading mask: %r' % (
                        data0 + data1 + mask))

            mask = struct.unpack('!BBBB', mask)

        if length:
            payload = yield from stream.read(length)
            if len(payload) != length:
                args = (length, len(payload))
                raise WebSocketError(
                    'Incomplete read: expected message of %s bytes, '
                    'got %s bytes' % args)
        else:
            payload = b''

        if payload:
            payload = bytearray(payload)

            if mask:
                for i in range(len(payload)):
                    payload[i] = payload[i] ^ mask[i % 4]

        return fin, opcode, payload

    def _receive(self):
        """Return the next text or binary message from the socket."""
        opcode = None
        result = bytearray()

        while True:
            try:
                frame = yield from self._receive_frame()
            except:
                if self._closed:
                    return
                raise
            if frame is None:
                if result:
                    raise WebSocketError('Peer closed connection unexpectedly')
                return

            f_fin, f_opcode, f_payload = frame

            if f_opcode in (self.OPCODE_TEXT, self.OPCODE_BINARY):
                if opcode is None:
                    opcode = f_opcode
                else:
                    raise WebSocketError(
                        'The opcode in non-fin frame is expected '
                        'to be zero, got %r' % (f_opcode, ))

            elif not f_opcode:
                if opcode is None:
                    self.close(1002)
                    raise WebSocketError('Unexpected frame with opcode=0')

            elif f_opcode == self.OPCODE_CLOSE:
                if len(f_payload) >= 2:
                    self.close_code = struct.unpack(
                        '!H', str(f_payload[:2]))[0]
                    self.close_message = f_payload[2:]
                elif f_payload:
                    raise WebSocketError(
                        'Invalid close frame: %s %s %s' % (
                            f_fin, f_opcode, repr(f_payload)))

                code = self.close_code
                if code is None or (code >= 1000 and code < 5000):
                    self.close()
                else:
                    self.close(1002)
                    raise WebSocketError(
                        'Received invalid close frame: %r %r' % (
                            code, self.close_message))
                return

            elif f_opcode == self.OPCODE_PING:
                self._send_frame(f_payload, opcode=self.OPCODE_PONG)
                continue

            elif f_opcode == self.OPCODE_PONG:
                continue

            else:
                raise WebSocketError("Unexpected opcode=%r" % (f_opcode, ))

            result.extend(f_payload)
            if f_fin:
                break

        if opcode == self.OPCODE_TEXT:
            return result, False
        elif opcode == self.OPCODE_BINARY:
            return result, True
        else:
            raise AssertionError(
                'internal serror in websocket: opcode=%r' % (opcode, ))

    @tulip.coroutine
    def receive(self):
        result = yield from self._receive()
        if not result:
            return

        message, is_binary = result
        if is_binary:
            return message
        else:
            try:
                return message.decode('utf-8')
            except ValueError:
                self.close(1007)
                raise

    def _send_frame(self, message, opcode):
        """Send a frame over the websocket with message as its payload"""
        header = bytes([0x80 | opcode])
        msg_length = len(message)

        if msg_length < 126:
            header += bytes([msg_length])
        elif msg_length < (1 << 16):
            header += bytes([126]) + struct.pack('!H', msg_length)
        elif msg_length < (1 << 63):
            header += bytes([127]) + struct.pack('!Q', msg_length)
        else:
            raise FrameTooLargeException()

        self._wstream.write(header + message)

    def send(self, message, binary=False):
        """Send a frame over the websocket with message as its payload"""
        if binary:
            return self._send_frame(message, self.OPCODE_BINARY)
        else:
            return self._send_frame(message, self.OPCODE_TEXT)

    def close(self, code=1000, message=b''):
        """Close the websocket, sending the specified code and message"""
        if not self._closed:
            self._send_frame(
                struct.pack('!H%ds' % len(message), code, message),
                opcode=self.OPCODE_CLOSE)
            self._closed = True
