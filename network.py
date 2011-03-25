import time
import socket
import json
import errno
import select

from socket import inet_aton, inet_ntoa, htons, ntohs, htonl, ntohl
from socket import AF_INET, SOCK_STREAM

import msgs
import storage
import protocol
import status
import timerq

from msgs import HEADER_LEN, HEADER_START, TYPE_TX, TYPE_BLOCK
from utils import *

PORT = 8333

class Node():
    """Implements handling of remote connections
        NodeDisconnected is thrown when a node is disconnected"""
    def __init__(self, host):
        self.initialized = False
        self.socket = socket.socket(AF_INET, SOCK_STREAM)
        self.socket.setblocking(False)
        try:
            self.socket.connect((host,PORT))
        except socket.error as e:
            if e.args[0] != errno.EINPROGRESS:
                raise
    def initialize(self):
        self.initialized = True
        try:
            peer = self.socket.getpeername()
        except socket.error as e:
            if e.args[0] == errno.ENOTCONN:
                self.close() # No client at other end
            raise
        self.address = msgs.Address.make(peer[0], peer[1])
        self.buffer = bytearray()
        self.outbuf = bytearray()
        self.peer = None
        self.in_flight = []
        self.sendmsg(msgs.Version.make(self.address))
    def fileno(self):
        return self.socket.fileno()
    def readmsg(self):
        start = self.buffer.find(HEADER_START)
        if start == -1:
            return None
        if start != 0:
            print("Gap found between msgs, containing",self.buffer[:start])
            self.close()
        if len(self.buffer) < HEADER_LEN:
            return None
        header = msgs.Header(self.buffer[:HEADER_LEN])
        if len(self.buffer) < header.len + HEADER_LEN:
            return None
        msgdata = self.buffer[:header.len + HEADER_LEN]
        body = msgdata[HEADER_LEN:]
        #assert msgs.serialize(header.deserialize(body)) == msgdata
        self.buffer = self.buffer[header.len + HEADER_LEN:]
        return header.deserialize(body)
    def sendmsg(self, msg):
        print("========US========")
        print(msg.tojson())
        self.outbuf.extend(msgs.serialize(msg))
    def writable(self):
        bytessent = self.socket.send(self.outbuf)
        self.outbuf = self.outbuf[bytessent:]
    def wantswrite(self):
        if not self.initialized:
            return False
        self.wants_send()
        return not self.outbuf == b""
    def readable(self):
        if not self.initialized:
            self.initialize()
        d = self.socket.recv(4096)
        if d == b"": # "the peer has performed an orderly shutdown"
            self.close()
        self.buffer.extend(d)
        try:
            msg = self.readmsg()
        except ProtocolViolation:
            self.close()
        if msg == None:
            return
        print("=======THEM=======")
        print(json.dumps(msg.tojson()))
        #msg.tojson() == msg.__class__.fromjson(msg.tojson()).tojson()
        getattr(self, "handle_" + msg.type)(msg)
    def close(self):
        self.socket.close()
        raise NodeDisconnected()
    
    def wants_send(self):
        "If you want to send a msg, do so"
        while (len(self.in_flight) < status.MAX_IN_FLIGHT and
                status.state.requestq):
            print(len(status.state.requestq))
            item = status.state.requestq.pop()
            self.in_flight.append(item)
            self.sendmsg(msgs.Getdata.make([item]))
    def handle_version(self, msg):
        if msg.version < 31900:
            self.close()
        if msg.sender == self.peer and msg.sender.port == PORT:
            pass #FIXME add to list of addresses
        self.sendmsg(msgs.Verack.make())
        self.sendmsg(msgs.Getblocks.make([status.genesisblock]))
    def handle_verack(self, msg):
        self.active = True
        #self.sendmsg(msgs.Getblocks.make([status.genesisblock]))
    def handle_addr(self, msg):
        storage.storeaddrs(msg.addrs)
    def handle_inv(self, msg):
        for obj in msg.objs:
            if obj.objtype == TYPE_TX and obj.hash not in status.txs:
                    status.state.requestq.appendleft(obj)
            if obj.objtype == TYPE_BLOCK and obj.hash not in status.blocks:
                    status.state.requestq.appendleft(obj)
        # Test weather it is a full response to GetBlocks
    def handle_block(self, msg):
        protocol.add_block(msg)
        iv = msgs.InvVect.make(TYPE_BLOCK, msg.block.hash)
        if iv in self.in_flight:
            self.in_flight.remove(iv)
    def handle_tx(self, msg):
        protocol.storetx(msg)

class NodeDisconnected(BaseException): pass


def mainloop():
    while True:
        writenodes = [node for node in nodes if node.wantswrite()]
        waitfor = timerq.wait_for()
        readable, writable, _ = select.select(nodes, writenodes, [], waitfor)
        timerq.do_events()
        for node in nodes:
            try:
                if node in readable:
                    node.readable()
                if node in writable:
                    node.writable()
            except NodeDisconnected:
                nodes.remove(node)

nodes = [
    Node('64.22.103.150'),
    Node('240.1.1.1'), # Unallocated by IANA, will fail to connect
]
