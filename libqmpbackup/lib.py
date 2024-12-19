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
import uuid
from json import dumps as json_dumps
from glob import glob
import logging
import logging.handlers
import colorlog
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


def setup_log(argv):
    """setup logging"""
    log_format_colored = (
        "%(green)s[%(asctime)s]%(reset)s%(blue)s %(log_color)s%(levelname)7s%(reset)s "
        "- %(funcName)s"
        ":%(log_color)s %(message)s"
    )
    log_format = "[%(asctime)-15s] %(levelname)7s - %(funcName)s  %(message)s"
    if argv.debug:
        loglevel = logging.DEBUG
    else:
        loglevel = logging.INFO
    stdout = logging.StreamHandler(stream=sys.stdout)
    handler = []
    formatter = colorlog.ColoredFormatter(
        log_format_colored,
        log_colors={
            "WARNING": "yellow",
            "ERROR": "red",
            "DEBUG": "cyan",
            "CRITICAL": "red",
        },
    )
    stdout.setFormatter(formatter)
    handler.append(stdout)
    if argv.syslog is True:
        handler.append(logging.handlers.SysLogHandler(address="/dev/log"))
    if argv.logfile != "":
        logpath = os.path.dirname(argv.logfile)
        if logpath != "":
            os.makedirs(logpath, exist_ok=True)
        handler.append(logging.FileHandler(argv.logfile, mode="a"))
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


def check_bitmap_uuid(bitmaps, backup_uuid):
    """Check if the UUID of the backup target folder matches the
    bitmap uuid"""
    for bitmap in bitmaps:
        if bitmap["name"].endswith(backup_uuid):
            return True

    return False


def check_bitmap_state(node, bitmaps):
    """Check if the bitmap state is ready for backup

    active  -> Ready for backup
    frozen  -> backup in progress
    disabled-> migration might be going on
    """
    status = False
    for bitmap in bitmaps:
        log.debug("Bitmap information: %s", json_pp(bitmap))
        try:
            status = "active" in bitmap["status"]
        except KeyError:
            status = bitmap["recording"]

        if bitmap["name"] == f"qmpbackup-{node}" and status is True:
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
    images = [
        os.path.join(argv.dir, f)
        for f in image_files
        if not (f.endswith(".config") or f == "uuid")
    ]
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
            "Partial backup file found, backup chain might be broken. "
            "Consider removing file before attempting to rebase."
        )
    if argv.filter == "":
        if "FULL-" not in images[0]:
            raise RuntimeError("Unable to find base FULL image in target folder.")

    if argv.filter != "":
        images = [x for x in images if argv.filter in x]
        images_flat = [x for x in images_flat if argv.filter in x]

    return images, images_flat


def copyfile(src, target):
    """Copy file"""
    try:
        shutil.copyfile(src, target)
    except shutil.Error as errmsg:
        raise RuntimeError(
            f"Failed to copy file [{src}] to [{target}]: {errmsg}"
        ) from errmsg


def save_uuid(target, use_uuid=""):
    """Create an unique uuid that is written to the backup target file and
    added to the generated bitmap name. So later incremental backups can
    check if the backup target directory is matching the backup chain"""
    uuidfile = os.path.join(target, "uuid")
    if use_uuid == "":
        backup_uuid = uuid.uuid4()
    else:
        backup_uuid = use_uuid
    try:
        with open(uuidfile, "w+", encoding="utf-8") as info_file:
            info_file.write(str(backup_uuid))
            log.info("Backup UUID: [%s]", backup_uuid)
    except IOError as errmsg:
        raise RuntimeError(f"Unable to store uuid: [{errmsg}]") from errmsg
    except Exception as errmsg:
        raise RuntimeError(errmsg) from errmsg

    return str(backup_uuid)


def get_uuid(target):
    """Read UUID to check if current existing bitmap chain matches the
    backup target folder during incremental backup"""
    uuidfile = os.path.join(target, "uuid")
    try:
        with open(uuidfile, "r", encoding="utf-8") as uuid_file:
            backup_uuid = uuid_file.read()
            log.info(
                "Current Backup UUID: [%s] for folder [%s]",
                backup_uuid,
                target,
            )
    except IOError as errmsg:
        raise RuntimeError(f"Failed to read file [{uuidfile}]") from errmsg
    except Exception as errmsg:
        raise RuntimeError(errmsg) from errmsg

    return str(backup_uuid)
