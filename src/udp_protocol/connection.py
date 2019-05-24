#!/usr/bin/env micropython
#!/usr/bin/env python3

# Untested on actual esp atm.
# Also still need to implement (really trying to think of a neat way) to
# implement the wrapper to let the data go in. I feel like the answer is somehow
# a coroutine but can't quite put my mental finger on how it will work.
# Also think about the EVIL try/except blocks that are in udp_receive for now...

# One thing to work on is the notion of being disconnected. Unconnected
# (initial) state is trivial, but to work out when disconnected is trickier but
# would be useful to stop sending loads of messages into the void.

# To note: in the case of receiving a burst of wrong score packets we respond to
# every single one - potentially flooding the medium. If say when we then change
# score immediately after sending the burst, all the echoes/acks from the
# receiver will then also be wrong, causing us to respond to each and every one
# of those with a response (that will also all be acked). This could continue
# until all messages are received and the score stops changing in less than the
# RTT. Potential solution: some kind of timeout on the sender side to prevent
# resending the same score over and over within a timeframe. If the score is
# continually changing then sending the new score is fine as we do want the
# update as fast as possible and we don't anticipate continuous changes with
# very little gap between them.
# All these arbitrary timeouts really suck. Perhaps try to shift some or all to
# depend on RTT somewhat. But then we need packets to contain ack data so we
# know if we send 2 packets which is being acked.
#
# Note also - on x86 micropython the random seeds are the same and so the id's
# generated for both sender and receiver are the same. I did not plan for this
# but by happy coincidence and when thought about this is no issue.
#

# TODO: haven't changed it for now during refactoring - but can we merge the
# receiver else case where it sends a sender = my_id and receiver =
# Packet.UNKNOWN_ID together with the standard case where we send the score?
# Looking at sender case I assume this would trigger either one of two cases:
# If already connected then this packet would appear like any other
# reply/lookout packet
# If either the sender or the receiver are not what the sender expects then it
# will go through the connection change/switching procedure

import sys
import time

from udp_receive import SimpleUDP
from sequence_numbers import SequenceNumber
from packet import Packet
from utility import gen_random, int_to_bytes, probability
from countdown_timer import make_countdown_timer

# class BaseConnection:
#     sock
#     my_id
#     rx_id
#     next_remote_seq
#     next_local_seq

#     def reset(*, rx_id = None, my_id = None): pass # new_rx_id
#     def send_data(payload): pass
#     def recv(timeout_ms): pass

#     # Sender - else response to lookout msg
#     p = Packet(
#         sender = my_id,
#         receiver = packet.sender,
#         id_change = new_rx_id)

#     # Receiver - on connection switch
#     # Packet(my_id, rx_id, old_my_id)
#     p = Packet(sender = packet.id_change, receiver = packet.sender, id_change = my_id)

#     # Receiver - on receiving unknown
#     p = Packet(sender = my_id, receiver = Packet.UNKNOWN_ID)

class BaseConnection:

    def __init__(self, sock, my_id = None, rx_id = Packet.UNKNOWN_ID,
            next_remote_seq = SequenceNumber(
                bytes_ = Packet.SEQUENCE_NUMBER_SIZE),
            next_local_seq = SequenceNumber(
                bytes_ = Packet.SEQUENCE_NUMBER_SIZE)):
        if my_id is None:
            my_id = gen_random(Packet.ID_SIZE, excluding = Packet.UNKNOWN_ID)

        self.sock = sock
        self.my_id = my_id
        self.rx_id = rx_id
        self.next_remote_seq = next_remote_seq
        self.next_local_seq = next_local_seq
        self.logging = True

    def recv(self, timeout_ms):
        return Packet.from_bytes(self.sock.recv(
            Packet.packet_size(), timeout_ms = timeout_ms))

    def _print(self, *args, **kwargs):
        if self.logging:
            print(*args, **kwargs)

    def send(self, payload):
        # print("Sending response data packet", packet)
        packet = Packet(sender = self.my_id, receiver = self.rx_id,
                sequence_number = self.next_local_seq.post_increment(),
                payload = payload)
        self._print("Sending:", packet)
        return self.sock.send(packet.__bytes__())

    # Sender only
    def send_id_change_response(self, id_change_packet, new_rx_id):
        packet = Packet(sender = self.my_id,
                receiver = id_change_packet.sender, id_change = new_rx_id,
                payload = int_to_bytes(-1, Packet.PAYLOAD_SIZE))
        self._print("Sending:", packet)
        return self.sock.send(packet.__bytes__())

    # Receiver only
    def change_and_send_connection_change(self, id_change_packet):
        old_my_id = self.my_id
        self.reset(my_id = id_change_packet.id_change,
                rx_id = id_change_packet.sender)
        packet = Packet(sender = id_change_packet.id_change,
                receiver = id_change_packet.sender, id_change = old_my_id,
                payload = int_to_bytes(-1, Packet.PAYLOAD_SIZE))
        self._print("Sending:", packet)
        return self.sock.send(packet.__bytes__())

    def reset(self, *, my_id = None, rx_id = Packet.UNKNOWN_ID):
        if my_id is not None:
            self.my_id = my_id
        self.rx_id = rx_id
        self.next_remote_seq = SequenceNumber(
                bytes_ = Packet.SEQUENCE_NUMBER_SIZE)
        self.next_local_seq = SequenceNumber(
                bytes_ = Packet.SEQUENCE_NUMBER_SIZE)

# We have a lot of (well - exclusively) hard coded timeouts
#
# Don't have:
#   Any way to tell latency (and therefore respond to it)
#   Any way to tell packet drop rate (and therefore respond to it)
#       From this any kind of way to react to the medium dropping - usually
#       congestion control is in here but for this at least obviously there is
#       none as it's just us
#
#   Thoughts on how to genericise
# We know there are only 3 kinds of packet, and 2 per sender and 2 per receiver
# (1 shared type)

def sender_loop(sock, get_score_func):
    # get_score_func must return bytes object of len Packet.PAYLOAD_SIZE that
    # will be sent across - ie. the score.

    # TODO now: immediately when connected send score

    conn = BaseConnection(sock)
    new_rx_id = Packet.UNKNOWN_ID

    new_connection_id_countdown = make_countdown_timer(seconds = 10,
            started = False)

    last_received_timer = make_countdown_timer(seconds = 16, started = False)
    connected = False

    resend_same_countdown = make_countdown_timer(seconds = 0.2, started = True)
    # We use this to identify sending "same packet" again
    last_payload_sent = None

    score = get_score_func()
    # latest_remote_score = None

    # TODO: add initial send on startup

    conn.send(score)

    while True:

        # print("Packet size:", Packet.packet_size())
        packet = Packet.from_bytes(sock.recv(Packet.packet_size(), timeout_ms = 3000))
        print("Received:", packet)
        old_score = score
        score = get_score_func()

        if new_connection_id_countdown.just_expired():
            print("New connection reply window expired, resetting new_rx_id")
            new_rx_id = Packet.UNKNOWN_ID
        if last_received_timer.just_expired():
            print("Disconnected")
            connected = False
            # Reset everything
            conn.reset()
            new_rx_id = Packet.UNKNOWN_ID

        if packet is None:
            if connected and old_score != score:
                print("Sending new score")
                conn.send(score)
                last_payload_sent = score

        elif packet.sender == conn.rx_id and packet.receiver == conn.my_id:
            if packet.sequence_number >= conn.next_remote_seq:
                last_received_timer.reset()
                conn.next_remote_seq = packet.sequence_number + 1
                print("Got good packet")
                if packet.payload != score:
                    print("GOT WRONG SCORE ----------------------------------")
                    assert type(packet.payload) is bytes and \
                            len(packet.payload) == Packet.PAYLOAD_SIZE
                    if score != last_payload_sent or \
                            resend_same_countdown.just_expired():
                        print("Sending response data packet")
                        conn.send(score)
                        last_payload_sent = score
                        resend_same_countdown.reset()
                    else:
                        print("Not sending updated score as would be duplicate "
                        "within timeout")
            else:
                print("Got old/duplicate packet")

        elif packet.sender == conn.rx_id and packet.receiver == Packet.UNKNOWN_ID:
            print("Got old discovery packet from current receiver - ignoring")
            assert False, "This should never happen anymore"

        elif packet.sender == new_rx_id and packet.receiver == conn.my_id \
                and packet.id_change != Packet.UNKNOWN_ID:
            conn.reset(rx_id = new_rx_id)
            new_rx_id = Packet.UNKNOWN_ID
            new_connection_id_countdown.stop()
            print("Switching connection - new receiver:", conn.rx_id)
            # This is not a control packet - we have no way to differentiate
            # (currently) this as one. So put the correct updated score in here
            print("Sending score")
            conn.send(score)
            connected = True
            last_received_timer.reset()
            last_payload_sent = None

        else:
            if new_rx_id == Packet.UNKNOWN_ID:
                new_connection_id_countdown.reset()
                new_rx_id = gen_random(Packet.ID_SIZE,
                        excluding = (Packet.UNKNOWN_ID, conn.rx_id))
                print("Genning new rx_id", new_rx_id)
            print("Responding with id_change packet to :", new_rx_id)
            conn.send_id_change_response(packet, new_rx_id)
            last_payload_sent = None

        print("------------------------------------------------")

def receiver_loop(sock):

    # f = open("receiver_score_output.txt", "w", buffering = 1)

    conn = BaseConnection(sock)
    lookout_timeout = make_countdown_timer(seconds = 1, started = True)
    score = bytes(Packet.PAYLOAD_SIZE)

    conn.send(score)
    while True:
        packet = conn.recv(timeout_ms = 1000)
        if packet is not None:
            print("Received:", packet)

        if packet is None: # Timed out or got garbled message
            print("None")
            if lookout_timeout.just_expired():
                lookout_timeout.reset()
                print("Sending lookout message")
                conn.send(score)

        elif packet.sender == conn.rx_id and packet.receiver == conn.my_id \
                and packet.id_change == Packet.UNKNOWN_ID:
            if packet.sequence_number >= conn.next_remote_seq:
                conn.next_remote_seq = packet.sequence_number + 1
                print("Got good packet")
                if packet.payload != score:
                    score = packet.payload
                    # if probability(0.2, True, False):
                    #     print("Setting wrong score for testing")
                    #     # For testing, set wrong score
                    #     score = int_to_bytes(-1, 9)
                    print("Updating score to", int.from_bytes(score,
                        sys.byteorder), "and echoing back")
                    # f.write(str(int.from_bytes(score, sys.byteorder)) + "\n")
                    conn.send(score)
                    lookout_timeout.reset()
            else:
                print("Got old/duplicate packet")

        elif packet.receiver == conn.my_id and packet.id_change != Packet.UNKNOWN_ID:
            print("Changing id", conn.my_id, "->", packet.id_change, "and sending id change")
            conn.change_and_send_connection_change(packet)

        else:
            print("Got unknown sending back my details")
            conn.send(score)

        print("------------------------------------------------")
        # time.sleep(0.1)

latest_score_score = 0
def latest_score():
    global latest_score_score
    # Micropython has no user defined attribs on funcs
    # https://docs.micropython.org/en/latest/genrst/core_language.html#user-defined-attributes-for-functions-are-not-supported
    if probability(0.8, True, False):
        latest_score_score += 1
        print("Latest score increased to", latest_score_score)
    return int_to_bytes(latest_score_score, Packet.PAYLOAD_SIZE)

def main(argv):

    if len(argv) > 1:
        print("Receiver")
        with SimpleUDP(2520, "127.0.0.1", 2521) as sock:
            receiver_loop(sock)

    else:
        # Problem atm is how to send stuff and receive and know stuff.
        print("Sender")
        with SimpleUDP(2521, "127.0.0.1", 2520) as sock:
            sender_loop(sock, latest_score)

if __name__ == "__main__":
    sys.exit(main(sys.argv))

# # Sender
#         if packet is None:
#             if connected and old_score != score:

#         elif packet.sender == rx_id and packet.receiver == my_id \
#                 and packet.id_change == Packet.UNKNOWN_ID:
#             if packet.sequence_number >= next_remote_seq:
#                 if packet.payload != score:
#                     if score != last_payload_sent or \
#                             resend_same_countdown.just_expired():
#                     else:
#             else:

#         elif packet.sender == rx_id and packet.receiver == Packet.UNKNOWN_ID:

#         elif packet.sender == new_rx_id and packet.receiver == my_id \
#                 and packet.id_change != Packet.UNKNOWN_ID:

#         else:
#             if new_rx_id == Packet.UNKNOWN_ID:
# # Receiver

#         if packet is None: # Timed out or got garbled message
#             if lookout_timeout.just_expired():

#         elif packet.sender == rx_id and packet.receiver == my_id \
#                 and packet.id_change == Packet.UNKNOWN_ID:
#             if packet.sequence_number >= next_remote_seq:
#                 if packet.payload != score:
#             else:

#         elif packet.receiver == my_id and packet.id_change != Packet.UNKNOWN_ID:

#         else: