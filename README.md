qmpbackup
=========

qmpbackup is designed to create live full and incremental backups of running
qemu virtual machines via QMP protocol. It makes use of the dirty-bitmap
feature introduced in later qemu versions. It was mostly created for
educational purposes and is by no means complete. It works with standalone 
qemu processes.

If you want to backup qemu virtual machines which are managed via `libvirt`,
see this project:

 https://github.com/abbbi/virtnbdbackup

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
disks to ```/tmp/backup/<disk-id>/FULL-<timestamp>```. It ensures
consistency by creating the bitmap and backup within one QMP transaction.

See the following discussion on the qmeu-block mailinglist regarding
this topic:

 https://lists.nongnu.org/archive/html/qemu-block/2016-11/msg00682.html

Bitmaps will be added with persistent option flag, which means they are stored
permanently and are available between virtual machine shutdowns.

Second step is to change some data within your virtual machine and let
qmpbackup create an incremental backup for you, this works by:

```
 qmpbackup --socket /path/socket backup --level inc --target /tmp/backup/
```

the changed delta since your last full (or inc) backup will be dumped to
```/tmp/backup/<disk-id>INC-<timestamp>```, the dirty-bitmap is automatically
cleared after this and you can continue creating further incremental backups by
re-issuing the command likewise.

Filesystem Quisce
-----------------

In case the virtual machine has an guest agent installed you can set the Qemu
Guest Agent socket (```--agent-socket```)  and request filesytem quisce via
```--quisce``` option:

```
  qmpbackup --socket /tmp/vm --agent-socket /tmp/qga.sock backup --level full --target /tmp/ --quisce
```

Restore
-------

Restoring your data is a matter of rebasing the created qcow images by
using standard tools such as qemu-img or ```qmprebase```.

A image backup based on a backup folder containing the following backups:

```
 /tmp/backup/ide0-hd0
 ├── FULL-1480542683
 ├── INC-1480542701
 └── INC-1480542712
```

can be rolled back by using ```qmprebase```, it uses common qemu tools to check
consistency and does a rollback of your image file:

```
 qmprebase  rebase --dir /tmp/backup/ide0-hd0
```

While rebasing the saveset chain is merged into your FULL image which then
contains the latest state and can be booted via Qemu again.

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

1) Using the QMP protocol it cannot be used together with libvirt as libvirt
exclusively uses the virtual machines monitor socket. See
[virtnbdbackup](https://github.com/abbbi/virtnbdbackup).

I think it will make sure
to provide a good implementation of the dirty-bitmap feature in the future.

2) Qemus ```drive-backup``` function does currently not support dumping
data as a stream, it also cannot work with fifo pipes as the blockdriver
expects functions as ftruncate and fseek to work on the target file.
