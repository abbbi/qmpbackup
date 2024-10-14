"""
 QEMU Monitor Protocol Python class

 Copyright (C) 2022 Michael Ablassmeier <abi@grinser.de>
 Copyright (C) 2009, 2010 Red Hat Inc.

 Authors:
  Michael Ablassmeier <abi@grinser.de>
  Luiz Capitulino <lcapitulino@redhat.com>

 Based on work by:
  Luiz Capitulino <lcapitulino@redhat.com>

 This work is licensed under the terms of the GNU GPL, version 2.  See
 the COPYING file in the top-level directory.
"""

import json
import errno
import socket


class QMPError(Exception):
    """Error Exception"""


class QMPConnectError(QMPError):
    """Error Exception"""


class QMPCapabilitiesError(QMPError):
    """Error Exception"""


class QMPTimeoutError(QMPError):
    """Error Exception"""


class QEMUMonitorProtocol:
    """Qemu QMP protocol Monitor"""

    def __init__(self, address):
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
        self.__sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.__sockfile = None

    def __json_read(self):
        """Read reply"""
        while True:
            data = self.__sockfile.readline()
            if not data:
                return None
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
        try:
            self.__sock.sendall(json.dumps(qmp_cmd).encode())
        except OSError as err:
            if err.errno == errno.EPIPE:
                return err
            raise socket.error(err)
        resp = self.__json_read()
        return resp

    def cmd(self, name, args=None):
        """
        Build a QMP command and send it to the QMP Monitor.

        @param name: command name (string)
        @param args: command arguments (dict)
        @param id: command id (dict, list, string or int)
        """
        qmp_cmd = {"execute": name}
        if args:
            qmp_cmd["arguments"] = args
        return self.cmd_obj(qmp_cmd)

    def command(self, cmd, **kwds):
        """Execute command"""
        ret = self.cmd(cmd, kwds)
        if "error" in ret:
            raise RuntimeError(ret["error"]["desc"])
        return ret["return"]

    def close(self):
        """Close handle"""
        self.__sock.close()
        self.__sockfile.close()

    timeout = socket.timeout

    def settimeout(self, timeout):
        """Set socket timeout"""
        self.__sock.settimeout(timeout)
