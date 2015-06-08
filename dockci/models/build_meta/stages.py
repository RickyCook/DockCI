"""
Stages in a Build
"""

import subprocess

from dockci.exceptions import AlreadyRunError


class BuildStageBase(object):
    """
    A logged stage to a build
    """

    def __init__(self, build):
        self.build = build
        self.returncode = None

    def runnable(self, handle):
        """ Executeable portion of the stage """
        raise NotImplementedError("You must override the 'runnable' method")

    def data_file_path(self):
        """
        File that stage output is logged to
        """
        return self.build.build_output_path().join(
            '%s.log' % self.slug  # pylint:disable=no-member
        )

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


class BuildStage(BuildStageBase):
    """ Ad-hoc build stage """

    def __init__(self, build, slug, runnable=None, workdir=None):
        super(BuildStage, self).__init__(build)
        self.slug = slug
        self.workdir = workdir
        self._runnable = runnable

    def runnable(self, *args, **kwargs):
        """ Wrapper for runnable to avoid ambiguity """
        return self._runnable(*args, **kwargs)

    @classmethod
    def from_command(cls, build, slug, cwd, cmd_args):
        """
        Create a BuildStage object from a system command
        """
        stage = CommandBuildStage(build, cwd, cmd_args)
        setattr(stage, 'slug', slug)
        return stage


class CommandBuildStage(BuildStageBase):  # pylint:disable=abstract-method
    """
    A build stage that represents a series of commands
    """

    # pylint:disable=arguments-differ
    def __init__(self, build, workdir, cmd_args):
        assert len(cmd_args) > 0, "cmd_args are given"
        super(CommandBuildStage, self).__init__(build)
        self.workdir = workdir
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
