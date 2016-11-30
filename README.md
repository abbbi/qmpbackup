qmpbackup
=========

qmpbackup is designed to create live full and incremental backups of running
qemu virtual machines via QMP protocol. It makes use of the dirty-bitmap
feature introduced in later qemu versions. It was mostly created for
educational purposes and is by no means complete.

Prerequisites
-------------

The virtual machine must be reachable via QMP protocol on a unix socket,
usually this happens by starting the virtual machine via:

```
    qemu-system-<arch> <options> -qmp unix:/path/socket,server,nowait
```

qmpbackup makes use of this socket to pass needed commands to the
virtual machine.

Usage
-----

In order to create a full backup use the following command:

```
 qmpbackup --socket /path/socket backup --level full --target /tmp/backup/
```

the command will create a new dirty bitmap and backup the virtual machines
first disk to /tmp/backup/FULL-<timestamp>. It ensures consistency by
creating the bitmap and the backup within one QMP transaction.

See the following discussion on the qmeu-block mailinglist regarding
this topic:

 https://lists.nongnu.org/archive/html/qemu-block/2016-11/msg00682.html

Second step is to change some data within your virtual machine and let
qmpbackup create an incremental backup for you, this works by:

```
 qmpbackup --socket /path/socket backup --level inc --target /tmp/backup/
```

the changed delta since your last full (or inc) backup will be dumped to
/tmp/backup/INC-<timestamp>, the dirty-bitmap is automatically cleared after
this and you can continue creating further incremental backups by re-issuing
the command likewise.

Restore
-------

Restoring your data is a matter of rebasing the created qcow images by
using standard tools such as qemu-img.

A image backup based on a backup folder containing the following backups:

```
 /tmp/backup/
 ├── FULL-1480542683
 ├── INC-1480542701
 └── INC-1480542712
```

is restored by:

```
 qemu-img rebase -b INC-1480542701 INC-1480542712
 qemu-img commit INC-1480542712
 qemu-img rebase -b FULL-1480542683  INC-1480542701
 qemu-img commit  INC-1480542701
```

After rebasing and committing the saveset chain your FULL image is restored to
the latest state and can be booted via qemu again.

Misc commands
-------------

In order to remove existing dirty-bitmaps use:

```
 qmpbackup --socket /tmp/vm cleanup --remove-bitmaps
```

see 

```
 qmpbackup --help 
```

for more information and commands.

Limitations
-----------

 1) Currently qmpbackup supports only vms with one disk, this should be changed
    so it handles multiple disks of the vm in a good way.
 2) Dirty-bitmaps are not saved through vm shutdowns currently, qmpbackup will
    fail accordingly if no bitmap is existing and an incremental backup is
    attempted. Newer qemu versions might change that behavior and will make
    bitmaps persistent.
 3) qmpbackup does not talk to the qemu agent in order to thaw the filesystem
    or make sure your backed up data is consistent up to a application level.
 4) Using the QMP protocol it cannot be used together with libvirt as libvirt
    exclusively uses the virtual machines monitor socket. Livirt however will
    make sure to provide a good implementation of the dirty-bitmap feature
    in the future. Any hints on this are appreciated.
