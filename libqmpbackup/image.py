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
import json
import logging
import subprocess
from time import time

log = logging.getLogger(__name__)


def get_info(filename):
    """Query original qemu image information, can be used to re-create
    the image during rebase operation with the same options as the
    original one."""
    try:
        return subprocess.check_output(
            ["qemu-img", "info", f"{filename}", "--output", "json", "--force-share"]
        )
    except subprocess.CalledProcessError as errmsg:
        raise RuntimeError from errmsg


def save_info(backupdir, blockdev):
    """Save qcow image information"""
    for dev in blockdev:
        infofile = f"{backupdir}/{dev.node}.config"
        try:
            info = get_info(dev.filename)
        except RuntimeError as errmsg:
            log.warning("Unable to get qemu image info: [%s]", errmsg)
            continue
        with open(infofile, "wb+") as info_file:
            info_file.write(info)
            log.info("Saved image info: [%s]", infofile)


def create(argv, backupdir, blockdev):
    """Create target image used by qmp blockdev-backup image to dump
    data and resturn a list of target images per-device, which will
    be used as parameter for QMP drive-backup operation"""

    opt = []
    dev_target = {}
    timestamp = int(time())
    for dev in blockdev:
        targetdir = f"{backupdir}/{dev.node}/"
        os.makedirs(targetdir, exist_ok=True)
        filename = (
            f"{argv.level.upper()}-{timestamp}-{os.path.basename(dev.filename)}.partial"
        )
        target = f"{targetdir}/{filename}"

        with open(f"{backupdir}/{dev.node}.config", "rb") as config_file:
            qcow_config = json.loads(config_file.read().decode())

        try:
            opt.append("-o")
            opt.append(f"compat={qcow_config['format-specific']['data']['compat']}")
        except KeyError as errmsg:
            log.warning("Unable apply QCOW specific compat option: [%s]", errmsg)

        try:
            opt.append("-o")
            opt.append(f"cluster_size={qcow_config['cluster-size']}")
        except KeyError as errmsg:
            log.warning("Unable apply QCOW specific cluster_size option: [%s]", errmsg)

        try:
            if qcow_config["format-specific"]["data"]["lazy-refcounts"]:
                opt.append("-o")
                opt.append("lazy_refcounts=on")
        except KeyError as errmsg:
            log.warning(
                "Unable apply QCOW specific lazy_refcounts option: [%s]", errmsg
            )

        cmd = [
            "qemu-img",
            "create",
            "-f",
            f"{dev.format}",
            f"{target}",
            "-o",
            f"size={dev.virtual_size}",
        ]
        cmd = cmd + opt

        try:
            log.info(
                "Create target backup image: [%s], virtual size: [%s]",
                target,
                dev.virtual_size,
            )
            log.debug(cmd)
            subprocess.check_output(cmd)
            dev_target[dev.node] = target
        except subprocess.CalledProcessError as errmsg:
            raise RuntimeError from errmsg

    return dev_target


def rebase(directory, dry_run, until):
    """Rebase and commit all images in a directory"""
    try:
        os.chdir(directory)
    except FileNotFoundError as errmsg:
        log.error(errmsg)
        return False

    image_files = filter(os.path.isfile, os.listdir(directory))
    images = [os.path.join(directory, f) for f in image_files]
    images_flat = [os.path.basename(f) for f in images]
    if until is not None and until not in images_flat:
        log.error(
            "Image file specified by --until option [%s] does not exist in backup directory",
            until,
        )
        return False

    # sort files by creation date
    images.sort(key=os.path.getmtime)
    images_flat.sort(key=os.path.getmtime)

    if dry_run:
        log.info("Dry run activated, not applying any changes")

    if len(images) == 0:
        log.error("No image files found in specified directory")
        return False

    if ".partial" in " ".join(images_flat):
        log.error("Partial backup file found, backup chain might be broken.")
        log.error("Consider removing file before attempting to rebase.")
        return False

    if "FULL-" not in images[0]:
        log.error("First image file is not a FULL base image")
        return False

    if "FULL-" in images[-1]:
        log.error("No incremental images found, nothing to commit")
        return False

    idx = len(images) - 1

    if until is not None:
        sidx = images_flat.index(until)
    for image in reversed(images):
        idx = idx - 1
        if until is not None and idx >= sidx:
            log.info("Skipping checkpoint: %s as requested with --until option", image)
            continue

        if images.index(image) == 0 or "FULL-" in images[images.index(image)]:
            log.info(
                "Rollback of latest [FULL]<-[INC] chain complete, ignoring older chains"
            )
            break

        log.debug('"%s" is based on "%s"', images[idx], image)

        # before rebase we check consistency of all files
        check_cmd = f"qemu-img check '{image}'"
        try:
            log.info(check_cmd)
            if not dry_run:
                subprocess.check_output(check_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Error while file check: %s", errmsg)
            return False

        try:
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[idx]}" "{image}" -u'
            )
            if not dry_run:
                subprocess.check_output(rebase_cmd, shell=True)
            log.info(rebase_cmd)
            commit_cmd = f"qemu-img commit '{image}'"
            log.info(commit_cmd)
            if not dry_run:
                subprocess.check_output(commit_cmd, shell=True)
                os.remove(image)
        except subprocess.CalledProcessError as errmsg:
            log.error("Error while rollback: %s", errmsg)
            return False

    return True
