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
import logging
import subprocess

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


def rebase(directory, dry_run, until):
    """Rebase and commit all images in a directory"""
    if not os.path.exists(directory):
        log.error("Unable to find target directory")
        return False

    os.chdir(directory)
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
