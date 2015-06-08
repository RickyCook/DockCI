"""
Stages in a Build
"""

import subprocess

from dockci.exceptions import AlreadyRunError


class BuildStage(object):
    """
    A logged stage to a build
    """
    returncode = None

    def __init__(self, slug, build, runnable=None):
        self.slug = slug
        self.build = build
        self.runnable = runnable

    def data_file_path(self):
        """
        File that stage output is logged to
        """
        return self.build.build_output_path().join('%s.log' % self.slug)

    def run(self):
        """
        Start the child process, streaming it's output to the associated file,
        and block until it returns
        """
        if self.returncode is not None:
            raise AlreadyRunError(self)

        self.build.build_output_path().ensure_dir()
        with self.data_file_path().open('wb') as handle:
            self.returncode = self.runnable(handle)

    @classmethod
    def from_command(cls, slug, build, cwd, cmd_args):
        """
        Create a BuildStage object from a system command
        """
        def runnable(handle):
            """
            Synchronously run one or more processes, streaming to the given
            handle, stopping and returning the exit code if it's non-zero.

            Returns 0 if all processes exit 0
            """
            def run_one_cmd(cmd_args_single):
                """
                Run a process
                """
                # TODO escape args
                handle.write(bytes(">CWD %s\n" % cwd, 'utf8'))
                handle.write(bytes(">>>> %s\n" % cmd_args_single, 'utf8'))
                handle.flush()

                proc = subprocess.Popen(cmd_args_single,
                                        cwd=cwd.strpath,
                                        stdout=handle,
                                        stderr=subprocess.STDOUT)
                proc.wait()
                return proc.returncode

            if isinstance(cmd_args[0], (tuple, list)):
                first_command = True
                for cmd_args_single in cmd_args:
                    if first_command:
                        first_command = False
                    else:
                        handle.write("\n".encode())

                    returncode = run_one_cmd(cmd_args_single)
                    if returncode != 0:
                        return returncode

                return 0

            else:
                return run_one_cmd(cmd_args)

        assert len(cmd_args) > 0, "cmd_args are given"
        return cls(slug=slug, build=build, runnable=runnable)
