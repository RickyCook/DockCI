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

    def __init__(self, slug, build, runnable=None, workdir=None):
        self.slug = slug
        self.build = build
        self.workdir = workdir
        self._runnable = runnable

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

        return self.returncode == 0

    def runnable(self, *args, **kwargs):
        """ Wrapper for runnable to avoid ambiguity """
        return self._runnable(*args, **kwargs)

    # TODO this should really be a subclass or some such
    @classmethod
    def from_command(cls, slug, build, cwd, cmd_args):
        """
        Create a BuildStage object from a system command
        """
        return CommandBuildStage(slug, build, cwd, cmd_args)


class CommandBuildStage(BuildStage):
    """
    A build stage that represents a series of commands
    """

    # pylint:disable=arguments-differ
    def __init__(self, slug, build, workdir, cmd_args):
        assert len(cmd_args) > 0, "cmd_args are given"
        super(CommandBuildStage, self).__init__(
            slug, build, workdir=workdir,
        )
        self.cmd_args = cmd_args

    def runnable(self, handle):
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
            handle.write(bytes(">CWD %s\n" % self.workdir, 'utf8'))
            handle.write(bytes(">>>> %s\n" % cmd_args_single, 'utf8'))
            handle.flush()

            proc = subprocess.Popen(cmd_args_single,
                                    cwd=self.workdir.strpath,
                                    stdout=handle,
                                    stderr=subprocess.STDOUT)
            proc.wait()
            return proc.returncode

        if isinstance(self.cmd_args[0], (tuple, list)):
            first_command = True
            for cmd_args_single in self.cmd_args:
                if first_command:
                    first_command = False
                else:
                    handle.write("\n".encode())

                returncode = run_one_cmd(cmd_args_single)
                if returncode != 0:
                    return returncode

            return 0

        else:
            return run_one_cmd(self.cmd_args)
