qmp commands to do backup via cbw

 qmp-shell -vp /tmp/socket < start-full

-> wait for block job to finish, then cleanup:

 qmp-shell -vp /tmp/socket < cleanup

-> start incremental backup

 qmp-shell -vp /tmp/socket < start-inc
 qmp-shell -vp /tmp/socket < cleanup
