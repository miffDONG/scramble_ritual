"""TUIO 1.1 /tuio/2Dobj sender with a built-in OSC encoder (no extra dependency).

One bundle per frame, same as reacTIVision: source, alive, set (per object), fseq.
  set s i x y a X Y A m r   ->  session, fiducial id, position 0..1, angle rad,
                                velocity, rotation speed, motion and rotation acceleration
"""

import socket
import struct


def _pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def _osc_string(s):
    return _pad(s.encode("utf-8"))


def osc_message(address, *args):
    tags = ","
    payload = b""
    for a in args:
        if isinstance(a, bool):
            raise TypeError("bool not supported")
        if isinstance(a, int):
            tags += "i"
            payload += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            payload += struct.pack(">f", a)
        else:
            tags += "s"
            payload += _osc_string(str(a))
    return _osc_string(address) + _osc_string(tags) + payload


def osc_bundle(messages):
    out = _osc_string("#bundle") + struct.pack(">Q", 1)     # timetag 1 = immediately
    for m in messages:
        out += struct.pack(">i", len(m)) + m
    return out


class TuioSender:
    def __init__(self, host="127.0.0.1", port=3333, source="Ritualvision"):
        self.addr = (host, port)
        self.source = source
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.fseq = 0

    def send(self, objects):
        msgs = [osc_message("/tuio/2Dobj", "source", self.source),
                osc_message("/tuio/2Dobj", "alive", *[int(o.sid) for o in objects])]
        for o in objects:
            msgs.append(osc_message("/tuio/2Dobj", "set", int(o.sid), int(o.fid),
                                    float(o.x), float(o.y), float(o.a),
                                    float(o.X), float(o.Y), float(o.A), float(o.m), float(o.r)))
        self.fseq += 1
        msgs.append(osc_message("/tuio/2Dobj", "fseq", self.fseq))
        data = osc_bundle(msgs)
        try:
            self.sock.sendto(data, self.addr)
        except OSError:
            pass
        return data

    def close(self):
        self.sock.close()
