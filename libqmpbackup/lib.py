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
import shutil
from json import dumps as json_dumps
from glob import glob
import logging
from libqmpbackup.qaclient import QemuGuestAgentClient

log = logging.getLogger(__name__)


def has_full(directory, filename):
    """Check if directory contains full backup, either by searching
    for files beginning with FULL* or the file name of the disk
    itself (if --no-symlink/--no-subdir is used)
    """
    if len(glob(f"{directory}/FULL*")) == 0 and not os.path.exists(
        os.path.join(directory, os.path.basename(filename))
    ):
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


def has_partial(backupdir):
    """Check if partial backup exists in target directory"""
    if os.path.exists(backupdir):
        if len(glob(f"{backupdir}/*.partial")) > 0:
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


def get_images(argv):
    """get images within backup folder"""
    os.chdir(argv.dir)
    image_files = filter(os.path.isfile, os.listdir(argv.dir))
    images = [os.path.join(argv.dir, f) for f in image_files]
    images_flat = [os.path.basename(f) for f in images]
    if argv.until is not None and argv.until not in images_flat:
        raise RuntimeError(
            "Image file specified by --until option "
            f"[{argv.until}] does not exist in backup directory"
        )

    # sort files by creation date
    images.sort(key=os.path.getmtime)
    images_flat.sort(key=os.path.getmtime)

    if len(images) == 0:
        raise RuntimeError("No image files found in specified directory")

    if ".partial" in " ".join(images_flat):
        raise RuntimeError(
            "Partial backup file found, backup chain might be broken."
            "Consider removing file before attempting to rebase."
        )
    if "FULL-" not in images[0]:
        raise RuntimeError("Unable to find base FULL image in target folder.")

    return images, images_flat


def copyfile(src, target):
    """Copy file"""
    try:
        shutil.copyfile(src, target)
    except shutil.Error as errmsg:
        raise RuntimeError(f"Error during file copy: {errmsg}") from errmsg
