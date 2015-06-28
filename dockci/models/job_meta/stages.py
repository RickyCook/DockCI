"""
Stages in a Job
"""

import logging
import subprocess

from requests.exceptions import ConnectionError

from dockci.exceptions import AlreadyRunError, DockerUnreachableError


class JobStageBase(object):
    """
    A logged stage to a job
    """

    def __init__(self, job):
        self.job = job
        self.returncode = None

    def runnable(self, handle):
        """ Executeable portion of the stage """
        raise NotImplementedError("You must override the 'runnable' method")

    def data_file_path(self):
        """
        File that stage output is logged to
        """
        return self.job.job_output_path().join(
            '%s.log' % self.slug  # pylint:disable=no-member
        )

    def run(self, expected_rc=0):
        """
        Start the child process, streaming it's output to the associated file,
        and block until it returns. Returns True if the return code matches
        ``expected_rc``. If ``expected_rc`` is None, always return true
        """
        # pylint:disable=no-member
        logging.getLogger('dockci.job.stages').debug(
            "Starting '%s' job stage for job '%s'",
            self.slug, self.job.slug,
        )

        if self.returncode is not None:
            raise AlreadyRunError(self)

        self.job.job_stage_slugs.append(self.slug)
        self.job.save()

        self.job.job_output_path().ensure_dir()
        with self.data_file_path().open('wb') as handle:
            self.returncode = self.runnable(handle)

        if expected_rc is None:
            return True

        success = self.returncode == expected_rc
        if not success:
            logging.getLogger('dockci.job.stages').debug(
                "Stage '%s' expected return of '%s'. Actually got '%s'",
                self.slug, expected_rc, self.returncode,
            )

        return success


class JobStage(JobStageBase):
    """ Ad-hoc job stage """

    def __init__(self, job, slug, runnable=None, workdir=None):
        super(JobStage, self).__init__(job)
        self.slug = slug
        self.workdir = workdir
        self._runnable = runnable

    def runnable(self, *args, **kwargs):
        """ Wrapper for runnable to avoid ambiguity """
        return self._runnable(*args, **kwargs)

    @classmethod
    def from_command(cls, job, slug, cwd, cmd_args):
        """
        Create a JobStage object from a system command
        """
        stage = CommandJobStage(job, cwd, cmd_args)
        setattr(stage, 'slug', slug)
        return stage


class CommandJobStage(JobStageBase):  # pylint:disable=abstract-method
    """
    A job stage that represents a series of commands
    """

    # pylint:disable=arguments-differ
    def __init__(self, job, workdir, cmd_args):
        assert len(cmd_args) > 0, "cmd_args are given"
        super(CommandJobStage, self).__init__(job)
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


class DockerStage(JobStageBase):
    """
    Wrapper around common Docker command process. Will send output lines to
    file, and optionally use callbacks to notify on each line, and
    completion
    """

    def runnable_docker(self):
        """ Commands to execute to get a Docker stream """
        raise NotImplementedError(
            "You must override the 'runnable_docker' method"
        )

    def on_line(self, line):
        """ Method called for each line in the output """
        pass

    def on_done(self, line):
        """
        Method called when the Docker command is complete. Line given is the
        last line that Docker gave
        """
        pass

    def runnable(self, handle):
        """
        Perform the Docker command given
        """
        try:
            output = self.runnable_docker()
        except ConnectionError as ex:
            raise DockerUnreachableError(self.job.docker_client, ex)

        if not output:
            return 0

        line = ''
        for line in output:
            if isinstance(line, bytes):
                handle.write(line)
            else:
                handle.write(line.encode())

            handle.flush()

            self.on_line(line)

        on_done_ret = self.on_done(line)
        if on_done_ret is not None:
            return on_done_ret

        elif line:
            return 0

        return 1
