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
- [Filesystem Quisce](#filesystem-quisce)
- [Backup Offline virtual machines](#backup-offline-virtual-machines)
- [Restore](#restore)
- [Misc commands](#misc-commands)
  - [List devices suitable for backup](#list-devices-suitable-for-backup)
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
 qmpbackup --socket /path/socket backup --level full --target /tmp/backup/
```

the command will create a new dirty bitmap and backup the virtual machines
disks to ```/tmp/backup/<disk-id>/FULL-<timestamp>```. It ensures
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

Filesystem Quisce
-----------------

In case the virtual machine has an guest agent installed you can set the QEMU
Guest Agent socket (*--agent-socket*)  and request filesystem quisce via
*--quisce* option:

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

Restore
-------

Restoring your data is a matter of rebasing the created qcow images by
using standard tools such as *qemu-img* or *qmprebase*.

A image backup based on a backup folder containing the following backups:

```
 /tmp/backup/ide0-hd0
 ├── FULL-1480542683
 ├── INC-1480542701
 └── INC-1480542712
```

can be rolled back by using *qmprebase*, it uses common QEMU tools to check
consistency and does a rollback of your image file:

```
 qmprebase  rebase --dir /tmp/backup/ide0-hd0
```

During rebase, the saveset chain is merged into your FULL image which then
contains the latest state and can be booted via QEMU again.

`Note:` It makes sense to copy the existing backup directory to a temporary
folder before rebasing, to not alter your existing backups.

Using the `--until` option rollback to a specific incremental point in 
time is possible:

```
 qmprebase  rebase --dir /tmp/backup/ide0-hd0 --until INC-1480542701
```

Misc commands
-------------

### List devices suitable for backup

```
 qmpbackup --socket /tmp/vm info --show blockdev
```

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
