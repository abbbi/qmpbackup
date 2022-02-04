# QEMU Monitor Protocol Python class
#
# Copyright (C) 2009, 2010 Red Hat Inc.
#
# Authors:
#  Luiz Capitulino <lcapitulino@redhat.com>
#
# This work is licensed under the terms of the GNU GPL, version 2.  See
# the COPYING file in the top-level directory.

import json
import errno
import socket
import sys


class QMPError(Exception):
    pass


class QMPConnectError(QMPError):
    pass


class QMPCapabilitiesError(QMPError):
    pass


class QMPTimeoutError(QMPError):
    pass


class QEMUMonitorProtocol:
    def __init__(self, address, debug=False):
        """
        Create a QEMUMonitorProtocol class.

        @param address: QEMU address, can be either a unix socket path (string)
                        or a tuple in the form ( address, port ) for a TCP
                        connection
        @param server: server mode listens on the socket (bool)
        @raise socket.error on socket connection errors
        @note No connection is established, this is done by the connect() or
              accept() methods
        """
        self.__address = address
        self._debug = debug
        self.__sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def __json_read(self):
        while True:
            data = self.__sockfile.readline()
            if not data:
                return
            resp = json.loads(data)
            return resp

    error = socket.error

    def connect(self):
        """
        Connect to the QMP Monitor and perform capabilities negotiation.

        @return QMP greeting dict
        @raise socket.error on socket connection errors
        @raise QMPConnectError if the greeting is not received
        @raise QMPCapabilitiesError if fails to negotiate capabilities
        """
        self.__sock.connect(self.__address)
        self.__sockfile = self.__sock.makefile()

    def cmd_obj(self, qmp_cmd):
        """
        Send a QMP command to the QMP Monitor.

        @param qmp_cmd: QMP command to be sent as a Python dict
        @return QMP response as a Python dict or None if the connection has
                been closed
        """
        if self._debug:
            print >> sys.stderr, "QMP:>>> %s" % qmp_cmd
        try:
            self.__sock.sendall(json.dumps(qmp_cmd).encode())
        except socket.error as err:
            if err[0] == errno.EPIPE:
                return
            raise socket.error(err)
        resp = self.__json_read()
        if self._debug:
            print >> sys.stderr, "QMP:<<< %s" % resp
        return resp

    def cmd(self, name, args=None, id=None):
        """
        Build a QMP command and send it to the QMP Monitor.

        @param name: command name (string)
        @param args: command arguments (dict)
        @param id: command id (dict, list, string or int)
        """
        qmp_cmd = {"execute": name}
        if args:
            qmp_cmd["arguments"] = args
        if id:
            qmp_cmd["id"] = id
        return self.cmd_obj(qmp_cmd)

    def command(self, cmd, **kwds):
        ret = self.cmd(cmd, kwds)
        if "error" in ret:
            raise Exception(ret["error"]["desc"])
        return ret["return"]

    def close(self):
        self.__sock.close()
        self.__sockfile.close()

    timeout = socket.timeout

    def settimeout(self, timeout):
        self.__sock.settimeout(timeout)
