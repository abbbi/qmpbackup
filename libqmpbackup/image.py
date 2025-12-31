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
            ["qemu-img", "info", f"{filename}", "--output", "json", "--force-share"],
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
    backup_targets = {}
    fleece_targets = {}
    timestamp = int(time())
    for dev in blockdev:

        nodname = dev.node
        if dev.node.startswith("#block"):
            log.warning(
                "No node name set for [%s], falling back to device name: [%s]",
                dev.filename,
                dev.device,
            )
            nodname = dev.device

        if argv.no_subdir is True:
            targetdir = backupdir
        else:
            targetdir = os.path.join(backupdir, nodname)
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

        fleece_filename = (
            f"{argv.level.upper()}-{timestamp}-{nodname}.fleece.{dev.format}"
        )
        fleece_targetfile = os.path.join(dev.path, fleece_filename)
        fleece_cmd = [
            "qemu-img",
            "create",
            "-f",
            f"{dev.format}",
            f"{fleece_targetfile}",
            "-o",
            f"size={dev.virtual_size}",
        ]

        try:
            log.info(
                "Create target backup image: [%s], virtual size: [%s]",
                target,
                dev.virtual_size,
            )
            log.debug(cmd)
            subprocess.check_output(cmd)
            backup_targets[dev.node] = target

            log.info(
                "Create fleece image: [%s], virtual size: [%s]",
                fleece_targetfile,
                dev.virtual_size,
            )
            log.debug(fleece_cmd)
            subprocess.check_output(fleece_cmd)
            fleece_targets[dev.node] = fleece_targetfile

        except subprocess.CalledProcessError as errmsg:
            raise RuntimeError from errmsg

    return backup_targets, fleece_targets


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


def _check(image, argv):
    """before rebase we check consistency of all files"""
    if argv.skip_check is True:
        log.info("Skipping image check")
        return
    check_cmd = f"qemu-img check '{image}'"
    try:
        log.info(check_cmd)
        subprocess.check_output(check_cmd, shell=True)
    except subprocess.CalledProcessError as errmsg:
        raise RuntimeError(f"Consistency check failed: {errmsg}") from errmsg


def _snapshot_exists(snapshot, image):
    """before rebase we check if an snapshot already exists"""
    check_cmd = f"qemu-img snapshot -l '{image}'"
    try:
        log.info(check_cmd)
        output = subprocess.check_output(check_cmd, shell=True)
    except subprocess.CalledProcessError as errmsg:
        raise RuntimeError(f"Consistency check failed: {errmsg}") from errmsg

    if snapshot in output.decode():
        return True

    return False


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
                f"qemu-img commit {argv.commitopt} -b "
                f'"{targetdir}/{os.path.basename(images[idx])}" '
                f'"{targetdir}/{os.path.basename(image)}"'
            )
            if argv.rate_limit != 0:
                commit_cmd = f"{commit_cmd} -r {argv.rate_limit}"
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
        _check(images[0], argv)
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
            _check(image, argv)
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
        _check(images[0], argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    if not _snapshot_exists("FULL-BACKUP", images[0]):
        snapshot_cmd = f'qemu-img snapshot -c "FULL-BACKUP" "{images[0]}"'
        log.info(snapshot_cmd)
        try:
            if not argv.dry_run:
                subprocess.check_output(snapshot_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase command failed: [%s]", errmsg)
            return False
    else:
        log.info("Skip creation of already existent full backup snapshot")

    for image in images[1:]:
        try:
            _check(image, argv)
        except RuntimeError as errmsg:
            log.error(errmsg)
            return False

        imagebase = os.path.basename(image)
        if imagebase.startswith("INC"):
            log.info("Using timestamp as provided by from image name")
            timestamp = int(imagebase.split("-")[1])
        else:
            log.info(
                "No timestamp provided in image name, use timestamp from filesystem"
            )
            timestamp = int(os.path.getctime(image))

        snapshot_name = datetime.datetime.fromtimestamp(timestamp).strftime(
            "%Y-%m-%d-%H%M%S"
        )
        snapshot_name = f"Backup-{snapshot_name}"

        try:
            snapshot_cmd = f'qemu-img snapshot -c "{snapshot_name}" "{images[0]}"'
            log.info(snapshot_cmd)
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[0]}" "{image}" -u'
            )
            log.info(rebase_cmd)
            commit_cmd = (
                f"qemu-img commit {argv.commitopt} -b " f'"{images[0]}" ' f'"{image}"'
            )
            if argv.rate_limit != 0:
                commit_cmd = f"{commit_cmd} -r {argv.rate_limit}"
            log.info(commit_cmd)
            if not argv.dry_run:
                subprocess.check_output(rebase_cmd, shell=True)
                subprocess.check_output(commit_cmd, shell=True)
                subprocess.check_output(snapshot_cmd, shell=True)
        except subprocess.CalledProcessError as errmsg:
            log.error("Rebase command failed: [%s]", errmsg)
            return False

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
        _check(images[0], argv)
    except RuntimeError as errmsg:
        log.error(errmsg)
        return False

    for image in images[1:]:
        try:
            _check(image, argv)
        except RuntimeError as errmsg:
            log.error(errmsg)
            return False

        try:
            rebase_cmd = (
                f'qemu-img rebase -f qcow2 -F qcow2 -b "{images[0]}" "{image}" -u'
            )
            log.info(rebase_cmd)
            commit_cmd = f"qemu-img commit {argv.commitopt} '{image}'"
            if argv.rate_limit != 0:
                commit_cmd = f"{commit_cmd} -r {argv.rate_limit}"
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
