![ci](https://github.com/abbbi/qmpbackup/actions/workflows/ci-ubuntu-latest.yml/badge.svg)

qmpbackup
=========

qmpbackup is designed to create live full and incremental backups of running
qemu virtual machines via QMP protocol. It makes use of the dirty-bitmap
feature introduced in later QEMU versions. It works with standalone QEMU
processes.

![Alt text](qmpbackup.jpg?raw=true "Title")

If you want to backup QEMU virtual machines managed by `libvirt`, see this
project:

 https://github.com/abbbi/virtnbdbackup
 

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Installation](#installation)
- [Prerequisites](#prerequisites)
- [Usage](#usage)
- [Monthly Backups](#monthly-backups)
- [Excluding disks from backup](#excluding-disks-from-backup)
- [Filesystem Freeze](#filesystem-freeze)
- [Backup Offline virtual machines](#backup-offline-virtual-machines)
- [UEFI / BIOS (pflash devices)](#uefi--bios-pflash-devices)
- [Restoring / Rebasing the images](#restoring--rebasing-the-images)
- [Restore / Rebase with merge](#restore--rebase-with-merge)
- [Restore / Rebase with snapshots](#restore--rebase-with-snapshots)
- [Misc commands and options](#misc-commands-and-options)
  - [Compressing backups](#compressing-backups)
  - [List devices suitable for backup](#list-devices-suitable-for-backup)
  - [Including raw devices](#including-raw-devices)
  - [List existing bitmaps](#list-existing-bitmaps)
  - [Cleanup bitmaps](#cleanup-bitmaps)
  - [Speed limit](#speed-limit)
- [Limitations](#limitations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

Installation
-------------

*qmpbackup* makes use of [qemu.qmp](https://gitlab.com/jsnow/qemu.qmp)

```
 python3 -m venv venv
 source venv/bin/activate
 pip3 install -r requirements.txt
 python3 setup.py install
```

Prerequisites
-------------

The virtual machine must be reachable via QMP protocol on a unix socket,
usually this happens by starting the virtual machine via:

```
 qemu-system-<arch> <options> -qmp unix:/path/socket,server,nowait
```

*qmpbackup* uses this socket to pass required commands to the virtual machine.

Usage
-----

In order to create a full backup use the following command:

```
# remove already existent bitmaps from prior full backups:
 qmpbackup --socket /path/to/socket cleanup --remove-bitmaps
# create a new full backup to an empty directory:
 qmpbackup --socket /path/to/socket backup --level full --target /tmp/backup/
```

the command will create a new unique dirty bitmap and backup the virtual
machines disks to ```/tmp/backup/<disk-id>/FULL-<timestamp>```. It ensures
consistency by creating the bitmap and backup within one QMP transaction.

Multiple disks attached to the virtual machine are backed up concurrently.

During full and incremental backup, bitmaps will be created with `persistent
option flag`. This means QEMU attempts to store them in the QCOW images, so
they are available between virtual machine shutdowns. The attached QCOW images
must be in qcow(v3) format, for this to work.

If you can't convert your QCOW images to newer formats, you still can use the
backup mode `copy`: it allows to execute a complete full backup but no further
incremental backups.

Second step is to change some data within your virtual machine and let
*qmpbackup* create an incremental backup for you, this works by:

```
 qmpbackup --socket /path/socket backup --level inc --target /tmp/backup/
```

The changed delta since your last full (or inc) backup will be dumped to
`/tmp/backup/<disk-id>INC-<timestamp>`, the dirty-bitmap is automatically
cleared after this and you can continue creating further incremental backups by
re-issuing the command likewise.

There is also the `auto` backup level which combines the `full` and `inc`
backup levels. If there's no existing bitmap for the VM, `full` will run. If a
bitmap exists, `inc` will be used.

Monthly Backups
-----------------
Using the `--monthly` flag with the `backup` command, backups will be placed in
monthly folders in a YYYY-MM format.  The above combined with the `auto` backup
level, backups will be created in monthly backup chains.

Executing the backup and the date being 2021-11, the following command: 

`qmpbackup --socket /path/socket backup --level auto --monthly --target /tmp/backup`

will place backups in the following backup path: `/tmp/backup/2021-11/`

When the date changes to 2021-12 and *qmpbackup* is executed, backups will be
placed in `/tmp/backup/2021-12/` and a new full backup will be created.

Excluding disks from backup
-----------------

Disks can be excluded from the backup by using the *--exclude* option, the name
must match the devices "node" name (use the *info --show blockdev* option to
get a list of attached block devices considered for backup)

If only specific disks should be saved, use the *--include* option.

Filesystem Freeze
-----------------

In case the virtual machine has an guest agent installed you can set the QEMU
Guest Agent socket (*--agent-socket*)  and request filesystem quiesce via
*--quiesce* option:

```
  qmpbackup --socket /tmp/vm --agent-socket /tmp/qga.sock backup --level full --target /tmp/ --quisce
```

Use the following options to QEMU to enable an guest agent socket:

```
   -chardev socket,path=/tmp/qga.sock,server,nowait,id=qga0 \
   -device virtio-serial \
   -device "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0" \
```

Backup Offline virtual machines
-------------------------------

If you want to backup virtual machines without the virtual machine being in
fully operational state, it is sufficient to bring up the QEMU process in
`prelaunch` mode (The QEMU blocklayer is operational but no code is executed):

```
 qemu-system-<arch> -S <options>
```

UEFI / BIOS (pflash devices)
-----------------------------

If the virtual machine uses UEFI, it usually has attached `pflash` devices
pointing to the UEFI firmware and variables files. These will be included in
the backup by default.



Restoring / Rebasing the images
-------

Restoring your data is a matter of rebasing the created qcow images by
using standard tools such as *qemu-img* or *qmprestore*.

A image backup based on a backup folder containing the following backups:

```
/tmp/backup/ide0-hd0/
├── FULL-1706260639-disk1.qcow2
├── INC-1706260646-disk1.qcow2
└── INC-1706260647-disk1.qcow2
```

can be rolled back by using *qmprestore*, it uses common QEMU tools to check
consistency and does a rollback of your image file:

```
 qmprestore rebase --dir /tmp/backup/ide0-hd0
```

After rebase you will find an symlink `/tmp/backup/image`, which points to the
latest image to use with qemu or other tools.

`Note:` It makes sense to copy the existing backup directory to a temporary
folder before rebasing, if you do not want to alter your existing backups.

Using the `--until` option rollback to a specific incremental point in 
time is possible:

```
 qmprestore rebase --dir /tmp/backup/ide0-hd0 --until INC-1480542701
```

Restore / Rebase with merge
-------

It is also possible to restore and rebase the backup files into a new target
file image, without altering the original backup files:

```
 qmprestore merge --dir /tmp/backup/ide0-hd0/ --targetfile /tmp/restore/disk1.qcow2
```

Restore / Rebase with snapshots
-------

Using the `snapshotrebase` functionality it is possible to rebase/commit the
images back into an full backup, but additionally the rebase process will
create an internal snapshot for the qemu image, for each incremental backup
applied.

This way it is easily possible to switch between the backup states after
rebasing.

```
 qmprestore snapshotrebase --dir /tmp/backup/ide0-hd0/
 [..]
 qemu-img snapshot -l /tmp/backup/ide0-hd0/FULL-1706260639-disk1.qcow2
 Snapshot list:
 ID        TAG               VM SIZE                DATE     VM CLOCK     ICOUNT
 1         FULL-BACKUP           0 B 2024-10-21 12:50:45 00:00:00.000          0
 2         INC-1729507368-disk1.qcow2      0 B 2024-10-21 12:50:45 00:00:00.000          0
 3         INC-1729507369-disk1.qcow2      0 B 2024-10-21 12:50:45 00:00:00.000          0
```

Misc commands and options
--------------------------

### Compressing backups

The `--compress` option can be used to enable compression for target files
during the `blockdev-backup` operation. This can save quite some storage space on
the created target images, but may slow down the backup operation.

```
 qmpbackup --socket /tmp/vm backup [..] --compress
```

### List devices suitable for backup

```
 qmpbackup --socket /tmp/vm info --show blockdev
```

### Including raw devices

Attached raw devices (format: raw) do not support incremental backup. The
only way to create backups for these devices is to create a complete full
backup.

By default `qmpbackup` will ignore such devices, but you can use the
`--include-raw` option to create a backup for those devices too.

Of course, if you create an incremental backup for these devices, the
complete image will be backed up.

### List existing bitmaps

To query existing bitmaps information use:

```
 qmpbackup --socket /tmp/vm info --show bitmaps
```

### Cleanup bitmaps

In order to remove existing dirty-bitmaps use:

```
 qmpbackup --socket /tmp/vm cleanup --remove-bitmaps
```

If you create a new backup chain (new full backup to an empty
directory) you should cleanup old bitmaps before.

### Speed limit

You can set an speed limit (bytes per second) for all backup operations to
limit throughput:

```
 qmpbackup --socket /tmp/vm backup [..] --speed-limit 2000000
```


Limitations
-----------

1) Using the QMP protocol it cannot be used together with libvirt as libvirt
exclusively uses the virtual machines monitor socket. See
[virtnbdbackup](https://github.com/abbbi/virtnbdbackup).

2) QEMUs ```drive-backup``` function does currently not support dumping
data as a stream, it also cannot work with fifo pipes as the blockdriver
expects functions like ftruncate and fseek to work on the target file, so the
backup target must be a directory.
