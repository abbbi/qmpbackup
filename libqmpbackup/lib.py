#!/usr/bin/env python3
"""
 qmpbackup: Full an incremental backup using Qemus
 dirty bitmap feature

 Copyright (C) 2022  Michael Ablassmeier

 Authors:
  Michael Ablassmeier <abi@grinser.de>

 This work is licensed under the terms of the GNU GPL, version 3.  See
 the LICENSE file in the top-level directory.
"""
import os
import sys
from json import dumps as json_dumps
from glob import glob
import logging
from libqmpbackup.qaclient import QemuGuestAgentClient

log = logging.getLogger(__name__)


def has_full(directory):
    """Check if directory contains full backup"""
    if len(glob(f"{directory}/FULL*")) == 0:
        return False

    return True


def setup_log(debug, logfile=None):
    """setup logging"""
    log_format = "[%(asctime)-15s] %(levelname)7s  %(message)s"
    if debug:
        loglevel = logging.DEBUG
    else:
        loglevel = logging.INFO
    handler = []
    handler.append(logging.StreamHandler(stream=sys.stdout))
    if logfile:
        os.makedirs(os.path.basename(logfile), exist_ok=True)
        handler.append(logging.FileHandler(logfile, mode="a"))
    logging.basicConfig(format=log_format, level=loglevel, handlers=handler)
    return logging.getLogger(__name__)


def json_pp(json):
    """human readable json output"""
    return json_dumps(json, indent=4, sort_keys=True)


def check_for_partial(backupdir, node):
    """Check if partial backup exists in target directory"""
    targetdir = f"{backupdir}/{node}"
    if os.path.exists(targetdir):
        if len(glob(f"{targetdir}/*.partial")) > 0:
            return True

    return False


def check_bitmap_state(node, bitmaps):
    """Check if the bitmap state is ready for backup

    active  -> Ready for backup
    frozen  -> backup in progress
    disabled-> migration might be going on
    """
    for bitmap in bitmaps:
        log.debug("Existing Bitmaps and states: %s", json_pp(bitmap))
        match = f"qmpbackup-{node}"
        try:
            status = "active" in bitmap["status"]
        except KeyError:
            status = bitmap["recording"]

        if bitmap["name"] == match and status is True:
            return True

    return status


def connect_qaagent(socket):
    """Setup Qemu Agent connection"""
    try:
        qga = QemuGuestAgentClient(socket)
        log.info("Guest Agent socket connected")
    except QemuGuestAgentClient.error as errmsg:
        log.warning('Unable to connect guest agent socket: "%s"', errmsg)
        return False

    log.info("Trying to ping guest agent")
    if not qga.ping(5):
        log.warning("Unable to reach Guest Agent: can't freeze file systems.")
        return False

    qga_info = qga.info()
    log.info("Guest Agent is reachable")
    if "guest-fsfreeze-freeze" not in qga_info:
        log.warning("Guest agent does not support required commands.")
        return False

    return qga


def quisce(qga):
    """Quisce VM filesystem"""
    fsstate = fsgetstate(qga)
    if fsstate == "frozen":
        log.warning("Filesystem is already frozen")
        return True

    try:
        reply = qga.fsfreeze("freeze")
        log.info('"%s" Filesystem(s) freezed', reply)
        return True
    except RuntimeError as errmsg:
        log.warning('Unable to freeze: "%s"', errmsg)

    return False


def thaw(qga):
    """Thaw filesystems"""
    fsstate = fsgetstate(qga)
    if fsstate == "thawed":
        log.info("Filesystem is already thawed, skipping.")
        return True
    try:
        reply = qga.fsfreeze("thaw")
        log.info('"%s" filesystem(s) thawed', reply)
        return True
    except RuntimeError as errmsg:
        log.warning('Unable to thaw filesystem: "%s"', errmsg)

    return False


def fsgetstate(qga):
    """Return filesystem state"""
    try:
        reply = qga.fsfreeze("status")
        return reply
    except RuntimeError as errmsg:
        log.warning("Unable to get Filesystem status: %s", errmsg)

    return None
