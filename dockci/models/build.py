"""
DockCI - CI, but with that all important Docker twist
"""

import json
import os
import os.path
import re
import subprocess
import tempfile

from datetime import datetime
from uuid import uuid1

import docker

from flask import url_for

from dockci.exceptions import AlreadyRunError
from dockci.models.job import Job
# TODO fix and reenable pylint check for cyclic-import
from dockci.server import APP, CONFIG
from dockci.util import bytes_human_readable, stream_write_status
from dockci.yaml_model import LoadOnAccess, Model, OnAccess


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
        return self.build.build_output_path() + ['%s.log' % self.slug]

    def run(self):
        """
        Start the child process, streaming it's output to the associated file,
        and block until it returns
        """
        if self.returncode is not None:
            raise AlreadyRunError(self)

        data_dir_path = os.path.join(*self.build.build_output_path())
        data_file_path = os.path.join(*self.data_file_path())

        os.makedirs(data_dir_path, exist_ok=True)
        with open(data_file_path, 'wb') as handle:
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
                proc = subprocess.Popen(cmd_args_single,
                                        cwd=cwd,
                                        stdout=handle,
                                        stderr=subprocess.STDOUT)
                proc.wait()
                return proc.returncode

            if isinstance(cmd_args[0], (tuple, list)):
                for cmd_args_single in cmd_args:
                    returncode = run_one_cmd(cmd_args_single)
                    if returncode != 0:
                        return returncode

                return 0

            else:
                return run_one_cmd(cmd_args)

        assert len(cmd_args) > 0, "cmd_args are given"
        return cls(slug=slug, build=build, runnable=runnable)


class Build(Model):  # pylint:disable=too-many-instance-attributes
    """
    An individual job build, and result
    """
    def __init__(self, job=None, slug=None):
        super(Build, self).__init__()

        assert job is not None, "Job is given"

        self.job = job
        self.job_slug = job.slug

        if slug:
            self.slug = slug

    slug = OnAccess(lambda _: str(uuid1()))
    job = OnAccess(lambda self: Job(self.job_slug))
    job_slug = OnAccess(lambda self: self.job.slug)  # TODO infinite loop
    create_ts = LoadOnAccess(generate=lambda _: datetime.now())
    start_ts = LoadOnAccess(default=lambda _: None)
    complete_ts = LoadOnAccess(default=lambda _: None)
    result = LoadOnAccess(default=lambda _: None)
    repo = LoadOnAccess(generate=lambda self: self.job.repo)
    commit = LoadOnAccess(default=lambda _: None)
    version = LoadOnAccess(default=lambda _: None)
    image_id = LoadOnAccess(default=lambda _: None)
    container_id = LoadOnAccess(default=lambda _: None)
    exit_code = LoadOnAccess(default=lambda _: None)
    build_stage_slugs = LoadOnAccess(default=lambda _: [])
    build_stages = OnAccess(lambda self: [
        BuildStage(slug=slug, build=self)
        for slug
        in self.build_stage_slugs
    ])
    git_author_name = LoadOnAccess(default=lambda _: None)
    git_author_email = LoadOnAccess(default=lambda _: None)
    git_committer_name = LoadOnAccess(default=lambda self:
                                      self.git_author_name)
    git_committer_email = LoadOnAccess(default=lambda self:
                                       self.git_author_email)
    # pylint:disable=unnecessary-lambda
    build_config = OnAccess(lambda self: BuildConfig(self))

    @property
    def state(self):
        """
        Current state that the build is in
        """
        if self.result is not None:
            return self.result
        elif self.start_ts is not None:
            return 'running'  # TODO check if running or dead
        else:
            return 'queued'  # TODO check if queued or queue fail

    _docker_client = None

    @property
    def docker_client(self):
        """
        Get the cached (or new) Docker Client object being used for this build
        """
        if not self._docker_client:
            self._docker_client = docker.Client(base_url=CONFIG.docker_host)

        return self._docker_client

    @property
    def build_output_details(self):
        """
        Details for build output artifacts
        """
        # pylint:disable=no-member
        output_files = (
            (name, os.path.join(*self.build_output_path() + ['%s.tar' % name]))
            for name in self.build_config.build_output.keys()
        )
        return {
            name: {'size': bytes_human_readable(os.path.getsize(path)),
                   'link': url_for('build_output_view',
                                   job_slug=self.job_slug,
                                   build_slug=self.slug,
                                   filename='%s.tar' % name,
                                   ),
                   }
            for name, path in output_files
            if os.path.isfile(path)
        }

    def data_file_path(self):
        # Add the job name before the build slug in the path
        data_file_path = super(Build, self).data_file_path()
        data_file_path.insert(-1, self.job_slug)
        return data_file_path

    def build_output_path(self):
        """
        Directory for any build output data
        """
        return self.data_file_path()[:-1] + ['%s_output' % self.slug]

    def queue(self):
        """
        Add the build to the queue
        """
        if self.start_ts:
            raise AlreadyRunError(self)

        from dockci.workers import run_build_worker
        # TODO fix and reenable pylint check for cyclic-import
        APP.workers.apply_async(run_build_worker, (self.job_slug, self.slug))

    def _run_now(self):
        """
        Worker func that performs the build
        """
        self.start_ts = datetime.now()
        self.save()

        try:
            with tempfile.TemporaryDirectory() as workdir:
                pre_build = (stage() for stage in (
                    lambda: self._run_prep_workdir(workdir),
                    lambda: self._run_git_info(workdir),
                    lambda: self._run_tag_version(workdir),
                    lambda: self._run_build(workdir),
                ))
                if not all(pre_build):
                    self.result = 'error'
                    return False

                if not self._run_test():
                    self.result = 'fail'
                    return False

                self.result = 'success'
                self.save()

                # Failing this doesn't indicade build failure
                # TODO what kind of a failure would this not working be?
                self._run_fetch_output()

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'error'
            self._error_stage('error')

            return False

        finally:
            try:
                self._run_cleanup()

            except Exception:  # pylint:disable=broad-except
                self._error_stage('cleanup_error')

            self.complete_ts = datetime.now()
            self.save()

    def _run_prep_workdir(self, workdir):
        """
        Clone and checkout the build
        """
        stage = self._stage(
            'git_prepare', workdir=workdir,
            cmd_args=(['git', 'clone', self.repo, workdir],
                      ['git', 'checkout', self.commit],
                      )
        )
        result = stage.returncode == 0

        # check for, and load build config
        build_config_file = os.path.join(workdir, BuildConfig.slug)
        if os.path.isfile(build_config_file):
            # pylint:disable=no-member
            self.build_config.load(data_file=build_config_file)
            self.build_config.save()

        return result

    def _run_git_info(self, workdir):
        """
        Get info about the current commit from git
        """

        def runnable(handle):
            """
            Execute git to retrieve info
            """
            def run_proc(*args):
                """
                Run, and wait for a process with default args
                """
                proc = subprocess.Popen(args,
                                        stdout=subprocess.PIPE,
                                        stderr=handle,
                                        cwd=workdir,
                                        )
                proc.wait()
                return proc

            largest_returncode = 0
            properties_empty = True

            properties = {
                'Author name': ('git_author_name', '%an'),
                'Author email': ('git_author_email', '%ae'),
                'Committer name': ('git_committer_name', '%cn'),
                'Committer email': ('git_committer_email', '%ce'),
            }
            for display_name, (attr_name, format_string) in properties.items():
                proc = run_proc('git', 'show',
                                '-s',
                                '--format=format:%s' % format_string,
                                'HEAD')

                largest_returncode = max(largest_returncode, proc.returncode)
                value = proc.stdout.read().decode().strip()

                if value != '' and proc.returncode == 0:
                    setattr(self, attr_name, value)
                    properties_empty = False
                    handle.write((
                        "%s is %s\n" % (display_name, value)
                    ).encode())

            if properties_empty:
                handle.write("No information about the git commit could be "
                             "derived\n".encode())

            else:
                self.save()

            return proc.returncode

        stage = self._stage('git_info', workdir=workdir, runnable=runnable)
        return stage.returncode == 0

    def _run_tag_version(self, workdir):
        """
        Try and add a version to the build, based on git tag
        """
        stage = self._stage(
            'git_tag', workdir=workdir,
            cmd_args=['git', 'describe', '--tags']
        )
        if not stage.returncode == 0:
            # TODO remove spoofed return
            return True  # stage result is irrelevant

        try:
            # TODO opening file to get this is kinda awful
            data_file_path = os.path.join(*stage.data_file_path())
            with open(data_file_path, 'r') as handle:
                line = handle.readline().strip()
                # TODO be more generic! GOSH
                if re.match(r'^v\d+\.\d+\.\d+$', line):
                    self.version = line
                    self.save()
                # Always return 'True', else non-re.match'd tags will
                # result in a return value of 'None'
                # As noted above, stage result is irrelevant
                return True

        except KeyError:
            pass

        # TODO don't spoof the return; just ignore output elsewhere
        return True  # stage result is irrelevant

    def _run_build(self, workdir):
        """
        Tell the Docker host to build
        """
        def on_done(line):
            """
            Check the final line for success, and image id
            """
            if line:
                if isinstance(line, bytes):
                    line = line.decode()

                line_data = json.loads(line)
                re_match = re.search(r'Successfully built ([0-9a-f]+)',
                                     line_data.get('stream', ''))
                if re_match:
                    self.image_id = re_match.group(1)
                    return True

            return False

        return self._run_docker(
            'build',
            # saved stream for debugging
            # lambda: open('docker_build_stream', 'r'),
            lambda: self.docker_client.build(path=workdir,
                                             rm=True,
                                             stream=True),
            on_done=on_done,
        )

    def _run_test(self):
        """
        Tell the Docker host to run the CI command
        """
        def start_container():
            """
            Create a container instance, attache to its outputs and then start
            it, returning the output stream
            """
            container_details = self.docker_client.create_container(
                self.image_id, 'ci'
            )
            self.container_id = container_details['Id']
            self.save()

            stream = self.docker_client.attach(self.container_id, stream=True)
            self.docker_client.start(self.container_id)

            return stream

        def on_done(_):
            """
            Check container exit code and return True on 0, or False otherwise
            """
            details = self.docker_client.inspect_container(self.container_id)
            self.exit_code = details['State']['ExitCode']
            self.save()
            return self.exit_code == 0

        return self._run_docker(
            'test',
            start_container,
            on_done=on_done,
        )

    def _run_docker(self,
                    docker_stage_slug,
                    docker_command,
                    on_line=None,
                    on_done=None):
        """
        Wrapper around common Docker command process. Will send output lines to
        file, and optionally use callbacks to notify on each line, and
        completion
        """
        def runnable(handle):
            """
            Perform the Docker command given
            """
            output = docker_command()

            line = ''
            for line in output:
                if isinstance(line, bytes):
                    handle.write(line)
                else:
                    handle.write(line.encode())

                handle.flush()

                if on_line:
                    on_line(line)

            if on_done:
                return on_done(line)

            elif line:
                return True

            return False

        return self._stage('docker_%s' % docker_stage_slug,
                           runnable=runnable).returncode

    def _run_fetch_output(self):
        """
        Fetches any output specified in build config
        """

        def runnable(handle):
            """
            Fetch/save the files
            """
            # pylint:disable=no-member
            mappings = self.build_config.build_output.items()
            for key, docker_fn in mappings:
                handle.write(
                    ("Fetching %s from '%s'..." % (key, docker_fn)).encode()
                )
                resp = self.docker_client.copy(self.container_id, docker_fn)

                if 200 <= resp.status < 300:
                    output_path = os.path.join(
                        *self.build_output_path() + ['%s.tar' % key]
                    )
                    with open(output_path, 'wb') as output_fh:
                        bytes_written = output_fh.write(resp.data)

                    handle.write(
                        (" DONE! %s total\n" % (
                            bytes_human_readable(bytes_written)
                        )).encode(),
                    )

                else:
                    handle.write(
                        (" FAIL! HTTP status %d: %s\n" % (
                            resp.status_code, resp.reason
                        )).encode(),
                    )

            # Output something on no output
            if not mappings:
                handle.write("No output files to fetch".encode())

        return self._stage('docker_fetch', runnable=runnable).returncode

    def _run_cleanup(self):
        """
        Clean up after the build/test
        """
        def cleanup_context(handle, object_type, object_id):
            """
            Get a stream_write_status context manager with messages set
            correctly
            """
            return stream_write_status(
                handle,
                "Cleaning up %s '%s'..." % (object_type, object_id),
                "DONE!",
                "FAILED!",
            )

        def runnable(handle):
            """
            Do the image/container cleanup
            """
            if self.container_id:
                with cleanup_context(handle, 'container', self.container_id):
                    self.docker_client.remove_container(self.container_id)

            if self.image_id:
                with cleanup_context(handle, 'image', self.image_id):
                    self.docker_client.remove_image(self.image_id)

        return self._stage('cleanup', runnable)

    def _error_stage(self, stage_slug):
        """
        Create an error stage and add stack trace for it
        """
        self.build_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()

        import traceback
        try:
            BuildStage(
                stage_slug,
                self,
                lambda handle: handle.write(
                    bytes(traceback.format_exc(), 'utf8')
                )
            ).run()
        except Exception:  # pylint:disable=broad-except
            print(traceback.format_exc())

    def _stage(self, stage_slug, runnable=None, workdir=None, cmd_args=None):
        """
        Create and save a new build stage, running the given args and saving
        its output
        """
        if cmd_args:
            stage = BuildStage.from_command(slug=stage_slug,
                                            build=self,
                                            cwd=workdir,
                                            cmd_args=cmd_args)
        else:
            stage = BuildStage(slug=stage_slug, build=self, runnable=runnable)

        self.build_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()
        stage.run()
        return stage


class BuildConfig(Model):  # pylint:disable=too-few-public-methods
    """
    Build config, loaded from the repo
    """

    slug = 'dockci.yaml'

    build = OnAccess(lambda self: Build(self.build_slug))
    build_slug = OnAccess(lambda self: self.build.slug)  # TODO infinite loop

    build_output = LoadOnAccess(default=lambda _: {})

    def __init__(self, build):
        super(BuildConfig, self).__init__()

        assert build is not None, "Build is given"

        self.build = build
        self.build_slug = build.slug

    def data_file_path(self):
        # Our data file path is <build output>/<slug>
        return self.build.build_output_path() + [BuildConfig.slug]
