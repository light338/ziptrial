# Execution boundaries

`inspect` reads archive bytes and metadata. It does not import archive modules,
execute entry points or read a manifest embedded in the archive. `run` uses only
the explicitly supplied external command manifest. Treat that manifest as code.

The archive is read into memory once, hashed and validated before extraction.
Regular files/directories only; names are checked for traversal, nonportable names,
file/directory collisions and case/Unicode collisions. Member size and total size
are bounded. Extraction writes individually with exclusive creation into a fresh
private temporary directory; it never calls extractall(). Execute bits are retained;
setuid/setgid bits and archive ownership are not restored.

The extraction has fresh HOME and temporary directories. The runner supplies PATH,
locale and temporary paths, disables user Python site packages and Git global/system
configuration, and does not copy arbitrary caller environment variables. System
libraries, executable search paths and site packages may still affect behavior.
A fresh extraction is not a hermetic dependency environment.

Commands have no stdin, run without shell interpolation and have bounded captured
output. POSIX children share a new process group; the group is stopped after exit,
timeout or output overflow. This is cleanup for ordinary programs, not containment
against malicious processes. Commands can read files, use network access or start
independent sessions. No claim of OS isolation, containerization or side-effect
rollback is made. Windows descendant cleanup is not validated.

Only two known path roots are redacted. Command arguments, output, source filenames
and hashes are recorded. Do not put credentials in argv or assume arbitrary output
is safe to publish. Archive/config/output should be on a filesystem you control;
the tool does not protect against another process racing to replace them.

The report hashes canonical JSON for the interpreted manifest (sorted keys, compact
encoding) and exact archive bytes. It includes an input file inventory, not a hash
of every intermediate filesystem state a build/check creates. A passing result means
only the registered commands passed against an extraction of those archive bytes
in the current system environment.
