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
import subprocess
from libqmpbackup.qaclient import QemuGuestAgentClient


class QmpBackup:
    """common functions"""

    def __init__(self, debug):
        self.debug = debug
        self._log = logging.getLogger(__name__)

    @staticmethod
    def has_full(directory):
        """Check if directory contains full backup"""
        if len(glob(f"{directory}/FULL*")) == 0:
            return False

        return True

    def setup_log(self, logfile=None):
        """setup logging"""
        log_format = "[%(asctime)-15s] %(levelname)7s  %(message)s"
        if self.debug:
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

    @staticmethod
    def json_pp(json):
        """human readable json output"""
        return json_dumps(json, indent=4, sort_keys=True)

    @staticmethod
    def check_for_partial(backupdir, node):
        """Check if partial backup exists in target directory"""
        targetdir = f"{backupdir}/{node}"
        if os.path.exists(targetdir):
            if len(glob(f"{targetdir}/*.partial")) > 0:
                return True

        return False

    def rebase(self, directory, dry_run, until):
        """Rebase and commit all images in a directory"""
        if not os.path.exists(directory):
            self._log.error("Unable to find target directory")
            return False

        os.chdir(directory)
        image_files = filter(os.path.isfile, os.listdir(directory))
        images = [os.path.join(directory, f) for f in image_files]
        images_flat = [os.path.basename(f) for f in images]
        if until is not None and until not in images_flat:
            self._log.error(
                "Image file specified by --until option [%s] does not exist in backup directory",
                until,
            )
            return False

        # sort files by creation date
        images.sort(key=os.path.getmtime)
        images_flat.sort(key=os.path.getmtime)

        if dry_run:
            self._log.info("Dry run activated, not applying any changes")

        if len(images) == 0:
            self._log.error("No image files found in specified directory")
            return False

        if ".partial" in " ".join(images_flat):
            self._log.error("Partial backup file found, backup chain might be broken.")
            self._log.error("Consider removing file before attempting to rebase.")
            return False

        if "FULL-" not in images[0]:
            self._log.error("First image file is not a FULL base image")
            return False

        if "FULL-" in images[-1]:
            self._log.error("No incremental images found, nothing to commit")
            return False

        idx = len(images) - 1

        if until is not None:
            sidx = images_flat.index(until)
        for image in reversed(images):
            idx = idx - 1
            if until is not None and idx >= sidx:
                self._log.info(
                    "Skipping checkpoint: %s as requested with --until option", image
                )
                continue

            if images.index(image) == 0 or "FULL-" in images[images.index(image)]:
                self._log.info(
                    "Rollback of latest [FULL]<-[INC] chain complete, ignoring older chains"
                )
                break

            self._log.debug('"%s" is based on "%s"', images[idx], image)

            # before rebase we check consistency of all files
            check_cmd = f"qemu-img check '{image}'"
            try:
                self._log.info(check_cmd)
                if not dry_run:
                    subprocess.check_output(check_cmd, shell=True)
            except subprocess.CalledProcessError as errmsg:
                self._log.error("Error while file check: %s", errmsg)
                return False

            try:
                rebase_cmd = (
                    f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[idx]}" "{image}" -u'
                )
                if not dry_run:
                    subprocess.check_output(rebase_cmd, shell=True)
                self._log.info(rebase_cmd)
                commit_cmd = f"qemu-img commit '{image}'"
                self._log.info(commit_cmd)
                if not dry_run:
                    subprocess.check_output(commit_cmd, shell=True)
            except subprocess.CalledProcessError as errmsg:
                self._log.error("Error while rollback: %s", errmsg)
                return False

        return True

    def check_bitmap_state(self, node, bitmaps):
        """Check if the bitmap state is ready for backup

        active  -> Ready for backup
        frozen  -> backup in progress
        disabled-> migration might be going on
        """
        for bitmap in bitmaps:
            self._log.debug("Existing Bitmaps and states: %s", self.json_pp(bitmap))
            match = f"qmpbackup-{node}"
            try:
                status = "active" in bitmap["status"]
            except KeyError:
                status = bitmap["recording"]

            if bitmap["name"] == match and status is True:
                return True

        return status

    def connect_qaagent(self, socket):
        """Setup Qemu Agent connection"""
        try:
            qga = QemuGuestAgentClient(socket)
            self._log.info("Guest Agent socket connected")
        except QemuGuestAgentClient.error as errmsg:
            self._log.warning('Unable to connect guest agent socket: "%s"', errmsg)
            return False

        self._log.info("Trying to ping guest agent")
        if not qga.ping(5):
            self._log.warning("Unable to reach Guest Agent: can't freeze file systems.")
            return False

        qga_info = qga.info()
        self._log.info("Guest Agent is reachable")
        if "guest-fsfreeze-freeze" not in qga_info:
            self._log.warning("Guest agent does not support required commands.")
            return False

        return qga

    def quisce(self, qga):
        """Quisce VM filesystem"""
        fsstate = self.fsgetstate(qga)
        if fsstate == "frozen":
            self._log.warning("Filesystem is already frozen")
            return True

        try:
            reply = qga.fsfreeze("freeze")
            self._log.info('"%s" Filesystem(s) freezed', reply)
            return True
        except Exception as errmsg:
            self._log.warning('Unable to freeze: "%s"', errmsg)

        return False

    def thaw(self, qga):
        """Thaw filesystems"""
        fsstate = self.fsgetstate(qga)
        if fsstate == "thawed":
            self._log.info("Filesystem is already thawed, skipping.")
            return True
        try:
            reply = qga.fsfreeze("thaw")
            self._log.info('"%s" filesystem(s) thawed', reply)
            return True
        except Exception as errmsg:
            self._log.warning('Unable to thaw filesystem: "%s"', errmsg)

        return False

    def fsgetstate(self, qga):
        """Return filesystem state"""
        try:
            reply = qga.fsfreeze("status")
            return reply
        except Exception as errmsg:
            self._log.warning("Unable to get Filesystem status: %s", errmsg)

        return None
