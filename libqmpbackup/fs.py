#!/usr/bin/env python3
"""
 qmpbackup: Full an incremental backup using Qemus
 dirty bitmap feature

 Copyright (C) 2024  Michael Ablassmeier

 Authors:
  Michael Ablassmeier <abi@grinser.de>

 This work is licensed under the terms of the GNU GPL, version 3.  See
 the LICENSE file in the top-level directory.
"""
import logging

log = logging.getLogger(__name__)


def get_state(qga):
    """Return filesystem state"""
    try:
        reply = qga.fsfreeze("status")
        return reply
    except RuntimeError as errmsg:
        log.warning("Unable to get Filesystem status: %s", errmsg)

    return None


def quiesce(qga):
    """Quiesce VM filesystem"""
    fsstate = get_state(qga)
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
    fsstate = get_state(qga)
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
