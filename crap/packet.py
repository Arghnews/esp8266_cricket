#!/usr/bin/env python3

import sys

from utility import gen_random, int_to_bytes
from sequence_numbers import SequenceNumber

class Packet:

    CONNECTION_ID_SIZE = 4
    SEQUENCE_NUMBER_SIZE = 4
    ACK_SIZE = 1
    MAXIMUM_PAYLOAD_SIZE = 9

    @classmethod
    def packet_size(cls):
        return sum(v for k, v in cls.__dict__.items() if k.isupper() and k.endswith("_SIZE"))

    def __str__(self):
        return ("connection_id: {:,} , ack bit: {}, sequence_number: {}, "
                "payload: {}").format(self.connection_id, self.ack,
                        self.sequence_number, self.payload)

    def __init__(self, *, ack = False, connection_id, sequence_number, payload):
        cls = type(self)
        while not connection_id:
            connection_id = gen_random(cls.CONNECTION_ID_SIZE)

        assert type(connection_id) is int
        assert connection_id < 2 ** (cls.CONNECTION_ID_SIZE * 8)
        assert type(sequence_number) is SequenceNumber
        assert len(bytes(sequence_number)) == cls.SEQUENCE_NUMBER_SIZE
        assert type(ack) is bool

        self.connection_id = connection_id
        self.sequence_number = sequence_number
        self.ack = ack
        cls.check_payload(payload)
        self.payload = payload

    @classmethod
    def check_payload(cls, payload):
        assert type(payload) is bytes
        assert len(payload) == cls.MAXIMUM_PAYLOAD_SIZE

    def __bytes__(self):
        cls = type(self)
        # Must have a valid payload to convert to bytes
        cls.check_payload(self.payload)

        ba = bytearray()
        # ba.append(len(self.payload) | (self.ack << 7)) # 1 byte
        ba += int_to_bytes(self.connection_id, cls.CONNECTION_ID_SIZE)
        ba += bytes(self.sequence_number)
        ba.append(self.ack)
        # ba += bytes(self.ack)
        assert len(ba) == cls.packet_size() - cls.MAXIMUM_PAYLOAD_SIZE

        ba += self.payload
        return bytes(ba)

    @classmethod
    def from_bytes(cls, bytes_):
        """Parses bytes as a Packet (header)
        Returns a header (with null payload) and the size of the payload to
        read next"""
        if bytes_ is None:
            return None
        assert len(bytes_) == cls.packet_size()

        # Python ints immutable, can't take by ref... How to create a neat
        # function to increment them inside it?
        offset = 0

        # If keeping these lines this should be neatened somehow, manual offset
        # is far too error-prone
        connection_id = int.from_bytes(
                bytes_[offset:offset + cls.CONNECTION_ID_SIZE], sys.byteorder)
        offset += cls.CONNECTION_ID_SIZE

        # TODO: change this when change SequenceNumber to bytes from bits
        sequence_number = SequenceNumber(
                n = bytes_[offset:offset + cls.SEQUENCE_NUMBER_SIZE],
                bits = cls.SEQUENCE_NUMBER_SIZE * 8)
        offset += cls.SEQUENCE_NUMBER_SIZE

        ack = bool(bytes_[offset])
        offset += cls.ACK_SIZE
        # print(offset, offset + cls.SEQUENCE_NUMBER_SIZE)
        # print(sequence_number)

        payload = bytes_[offset:offset + cls.MAXIMUM_PAYLOAD_SIZE]
        offset += cls.MAXIMUM_PAYLOAD_SIZE

        return cls(connection_id = connection_id, ack = ack,
                sequence_number = sequence_number, payload = payload)

    def __eq__(self, other):
        if type(other) is not type(other):
            return NotImplemented
        return self.__dict__ == other.__dict__

def same(self, other, *attrs):
    return all(getattr(self, attr) == getattr(other, attr) for attr in attrs)

def main(argv):

    sn = SequenceNumber(n = 5, bits = 32)
    p1 = Packet(connection_id = (1 << 31) + (1 << 24) + (1 << 16) + (1 << 8) + (1 << 4),
            sequence_number = sn, ack = False, payload = bytes(range(9)))
    print(Packet.packet_size())

    sn2 = SequenceNumber(n = 5, bits = 32)

    p2 = Packet(connection_id = (1 << 31) + (1 << 24) + (1 << 16) + (1 << 8) + (1 << 4),
            sequence_number = sn2, ack = False, payload = bytes(range(9)))

#     # No vars in micropython
#     print(vars(p1))
#     print(vars(p2))
#     print(vars(p1) == vars(p2))
#     print("my_vars:", p1.__dict__)
#     print([attr for attr in dir(p1) if not callable(getattr(p1, attr)) and not attr.startswith("__")])
#     print(vars(p1))
#     print(same(p1, p2))
#     return

    print("Hello world!")

    payload = bytes(range(9))

    # print(bytes(sn))
    # return

    p = Packet(connection_id = (1 << 31) + (1 << 24) + (1 << 16) + (1 << 8) + (1 << 4),
            sequence_number = sn, ack = False, payload = payload)
            # payload = bytes([1,2,3]))
    print(p)
    data = bytes(p)
    print(data)

    packet = Packet.from_bytes(data)

    print("packet:", packet)
    print("p:", p)
    assert packet == p

if __name__ == "__main__":
    sys.exit(main(sys.argv))