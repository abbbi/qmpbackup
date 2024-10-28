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
import datetime
from time import time
from libqmpbackup import lib

log = logging.getLogger(__name__)


def get_info(filename):
    """Query original qemu image information, can be used to re-create
    the image during backup operation with the same options as the
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
        infofile = os.path.join(backupdir, f"{os.path.basename(dev.filename)}.config")

        if dev.driver == "rbd":
            log.info("Skip saving image information for RBD device: [%s]", dev.filename)
            continue

        info = get_info(dev.filename)
        try:
            with open(infofile, "wb+") as info_file:
                info_file.write(info)
                log.info("Saved image info: [%s]", infofile)
        except IOError as errmsg:
            raise RuntimeError(f"Unable to store qcow config: [{errmsg}]") from errmsg
        except Exception as errmsg:
            raise RuntimeError(errmsg) from errmsg


def _get_options_cmd(backupdir, dev):
    """Read options to apply for backup target image from
    qcow image info json output"""
    opt = []
    with open(
        os.path.join(backupdir, f"{os.path.basename(dev.filename)}.config"), "rb"
    ) as config_file:
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
        log.warning("Unable apply QCOW specific lazy_refcounts option: [%s]", errmsg)

    return opt


def create(argv, backupdir, blockdev):
    """Create target image used by qmp blockdev-backup image to dump
    data and returns a list of target images per-device, which will
    be used as parameter for QMP drive-backup operation"""
    opt = []
    dev_target = {}
    timestamp = int(time())
    for dev in blockdev:
        if argv.no_subdir is True:
            targetdir = backupdir
        else:
            targetdir = os.path.join(backupdir, dev.node)
        os.makedirs(targetdir, exist_ok=True)
        if argv.no_timestamp and argv.level in ("copy", "full"):
            filename = f"{os.path.basename(dev.filename)}.partial"
        else:
            filename = f"{argv.level.upper()}-{timestamp}-{os.path.basename(dev.filename)}.partial"
        target = os.path.join(targetdir, filename)
        if dev.format != "raw":
            opt = opt + _get_options_cmd(backupdir, dev)

        cmd = [
            "qemu-img",
            "create",
            "-f",
            f"{dev.format}",
            f"{target}",
            "-o",
            f"size={dev.virtual_size}",
        ]
        if dev.format != "raw":
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


def clone(image, targetfile):
    """Copy base image for restore into new image file"""
    if os.path.exists(targetfile):
        log.error("Target file [%s] already exists, won't overwrite", targetfile)
        return False

    log.info("Copy source image [%s] to image file: [%s]", image, targetfile)

    try:
        lib.copyfile(image, targetfile)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    return True


def _check(image):
    """before rebase we check consistency of all files"""
    check_cmd = f"qemu-img check '{image}'"
    try:
        log.info(check_cmd)
        subprocess.check_output(check_cmd, shell=True)
    except subprocess.CalledProcessError as errmsg:
        raise RuntimeError(f"Consistency check failed: {errmsg}") from errmsg


def merge(argv):
    """Merge all files into new base image"""
    try:
        images, images_flat = lib.get_images(argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    idx = len(images) - 1
    if argv.until is not None:
        sidx = images_flat.index(argv.until)

    targetdir = os.path.dirname(argv.targetfile)
    if not clone(images[0], argv.targetfile):
        return False
    images[0] = argv.targetfile

    for image in reversed(images):
        idx = idx - 1
        if argv.until is not None and idx >= sidx:
            log.info("Skipping checkpoint: %s as requested with --until option", image)
            continue

        if images.index(image) == 0 or argv.targetfile in images[images.index(image)]:
            log.info(
                "Rollback of latest [FULL]<-[INC] chain complete, ignoring older chains"
            )
            break

        log.debug('"%s" is based on "%s"', image, images[idx])

        tgtfile = os.path.join(targetdir, os.path.basename(image))
        if not os.path.exists(tgtfile):
            if not clone(image, tgtfile):
                return False

        tgtfile = os.path.join(targetdir, os.path.basename(images[idx]))
        if not os.path.exists(tgtfile):
            if not clone(images[idx], tgtfile):
                return False

        try:
            rebase_cmd = (
                "qemu-img rebase -f qcow2 -F qcow2 -b "
                f'"{targetdir}/{os.path.basename(images[idx])}" '
                f'"{targetdir}/{os.path.basename(image)}" -u'
            )
            log.info(rebase_cmd)
            subprocess.check_output(rebase_cmd, shell=True)
            commit_cmd = (
                "qemu-img commit -b "
                f'"{targetdir}/{os.path.basename(images[idx])}" '
                f'"{targetdir}/{os.path.basename(image)}"'
            )
            log.info(commit_cmd)
            subprocess.check_output(commit_cmd, shell=True)
            if image != argv.targetfile:
                log.info(
                    "Removing temporary file after merge: [%s]",
                    os.path.join(targetdir, os.path.basename(image)),
                )
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase or commit command failed: [%s]", errmsg)
            return False

    return True


def rebase(argv):
    """Rebase all images in a directory without merging
    the data back into the base image"""
    link = os.path.join(argv.dir, "image")
    if os.path.exists(link):
        log.error("Directory has already been rebased: [%s]", link)
        return False

    try:
        images, images_flat = lib.get_images(argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    if "FULL-" in images[-1]:
        log.error("No incremental images found, nothing to rebase.")
        return False

    idx = len(images) - 1

    try:
        _check(images[0])
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    if argv.until is not None:
        sidx = images_flat.index(argv.until)

    for image in reversed(images):
        idx = idx - 1
        if argv.until is not None and idx >= sidx:
            log.info("Skipping checkpoint: %s as requested with --until option", image)
            continue

        if images.index(image) == 0 or "FULL-" in images[images.index(image)]:
            log.info(
                "Rollback of latest [FULL]<-[INC] chain complete, ignoring older chains"
            )
            log.info("You can use [%s] to access the latest image data.", link)
            break

        log.debug('"%s" is based on "%s"', image, images[idx])

        try:
            _check(image)
        except RuntimeError as errmsg:
            log.error(errmsg)
            return False

        try:
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[idx]}" "{image}" -u'
            )
            log.info(rebase_cmd)
            if not argv.dry_run:
                subprocess.check_output(rebase_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase command failed: [%s]", errmsg)
            return False

    if not argv.dry_run:
        try:
            os.symlink(images[-1], "image")
        except OSError as errmsg:
            logging.warning("Unable to create symlink to latest image: [%s]", errmsg)

    return True


def snapshot_rebase(argv):
    """Rebase the images, commit all changes but create a snapshot
    prior"""
    try:
        images, _ = lib.get_images(argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    if "FULL-" in images[-1] or len(images) == 1:
        log.error("No incremental images found, nothing to rebase.")
        return False

    try:
        _check(images[0])
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    snapshot_cmd = f'qemu-img snapshot -c "FULL-BACKUP" "{images[0]}"'
    log.info(snapshot_cmd)
    try:
        if not argv.dry_run:
            subprocess.check_output(snapshot_cmd, shell=True)
    except subprocess.CalledProcessError as errmsg:
        log.error("Rebase command failed: [%s]", errmsg)
        return False

    for image in images[1:]:
        try:
            _check(image)
        except RuntimeError as errmsg:
            log.error(errmsg)
            return False

        timestamp = int(os.path.basename(image).split("-")[1])
        snapshot_name = datetime.datetime.fromtimestamp(timestamp).strftime(
            "%Y-%m-%d-%H:%M:%S"
        )

        try:
            snapshot_cmd = f'qemu-img snapshot -c "{snapshot_name}" "{images[0]}"'
            log.info(snapshot_cmd)
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[0]}" "{image}" -u'
            )
            log.info(rebase_cmd)
            commit_cmd = "qemu-img commit -b " f'"{images[0]}" ' f'"{image}"'
            log.info(commit_cmd)
            if not argv.dry_run:
                subprocess.check_output(rebase_cmd, shell=True)
                subprocess.check_output(commit_cmd, shell=True)
                subprocess.check_output(snapshot_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase command failed: [%s]", errmsg)
            return False

        if not argv.dry_run:
            log.info("Removing: [%s]", image)
            os.remove(image)

        if argv.until is not None and os.path.basename(image) == argv.until:
            log.info(
                "Stopping at checkpoint: %s as requested with --until option", image
            )
            break

    return True


def commit(argv):
    """Rebase and commit all changes"""
    try:
        images, _ = lib.get_images(argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    if "FULL-" in images[-1] or len(images) == 1:
        log.error("No incremental images found, nothing to rebase.")
        return False

    try:
        _check(images[0])
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    for image in images[1:]:
        try:
            _check(image)
        except RuntimeError as errmsg:
            log.error(errmsg)
            return False

        try:
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[0]}" "{image}" -u'
            )
            log.info(rebase_cmd)
            commit_cmd = "qemu-img commit -b " f'"{images[0]}" ' f'"{image}"'
            log.info(commit_cmd)
            if not argv.dry_run:
                subprocess.check_output(rebase_cmd, shell=True)
                subprocess.check_output(commit_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase command failed: [%s]", errmsg)
            return False

        if not argv.dry_run:
            log.info("Removing: [%s]", image)
            os.remove(image)

        if argv.until is not None and os.path.basename(image) == argv.until:
            log.info(
                "Stopping at checkpoint: %s as requested with --until option", image
            )
            break

    return True
