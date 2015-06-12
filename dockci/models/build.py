"""
DockCI - CI, but with that all important Docker twist
"""

import logging
import random
import tempfile

from datetime import datetime

import docker
import py.path  # pylint:disable=import-error

from docker.utils import kwargs_from_env
from flask import url_for
from yaml_model import (LoadOnAccess,
                        Model,
                        ModelReference,
                        OnAccess,
                        ValidationError,
                        )

from dockci.exceptions import AlreadyRunError
from dockci.models.build_meta.config import BuildConfig
from dockci.models.build_meta.stages import BuildStage
from dockci.models.build_meta.stages_main import (BuildDockerStage,
                                                  TestStage,
                                                  )
from dockci.models.build_meta.stages_post import (PushStage,
                                                  FetchStage,
                                                  CleanupStage,
                                                  )
from dockci.models.build_meta.stages_prepare import (GitChangesStage,
                                                     GitInfoStage,
                                                     ProvisionStage,
                                                     TagVersionStage,
                                                     WorkdirStage,
                                                     )
from dockci.models.job import Job
# TODO fix and reenable pylint check for cyclic-import
from dockci.server import CONFIG
from dockci.util import bytes_human_readable, is_docker_id


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

    slug = OnAccess(lambda _: hex(int(datetime.now().timestamp() * 10000))[2:])
    job = OnAccess(lambda self: Job(self.job_slug))
    job_slug = OnAccess(lambda self: self.job.slug)  # TODO infinite loop
    ancestor_build = ModelReference(lambda self: Build(
        self.job,
        self.ancestor_build_slug
    ), default=lambda _: None)
    create_ts = LoadOnAccess(generate=lambda _: datetime.now())
    start_ts = LoadOnAccess(default=lambda _: None)
    complete_ts = LoadOnAccess(default=lambda _: None)
    result = LoadOnAccess(default=lambda _: None)
    repo = LoadOnAccess(generate=lambda self: self.job.repo)
    commit = LoadOnAccess(default=lambda _: None)
    tag = LoadOnAccess(default=lambda _: None)
    image_id = LoadOnAccess(default=lambda _: None)
    container_id = LoadOnAccess(default=lambda _: None)
    exit_code = LoadOnAccess(default=lambda _: None)
    docker_client_host = LoadOnAccess(
        generate=lambda self: self.docker_client.base_url,
    )
    build_stage_slugs = LoadOnAccess(generate=lambda _: [])
    build_stages = OnAccess(lambda self: [
        BuildStage(build=self, slug=slug)
        for slug
        in self.build_stage_slugs
    ])
    git_author_name = LoadOnAccess(default=lambda _: None)
    git_author_email = LoadOnAccess(default=lambda _: None)
    git_committer_name = LoadOnAccess(default=lambda self:
                                      self.git_author_name)
    git_committer_email = LoadOnAccess(default=lambda self:
                                       self.git_author_email)
    git_changes = LoadOnAccess(default=lambda _: None)
    # pylint:disable=unnecessary-lambda
    build_config = OnAccess(lambda self: BuildConfig(self))

    _provisioned_containers = []

    def validate(self):
        with self.parent_validation(Build):
            errors = []

            if not self.job:
                errors.append("Parent job not given")
            if self.image_id and not is_docker_id(self.image_id):
                errors.append("Invalid Docker image ID")
            if self.container_id and not is_docker_id(self.container_id):
                errors.append("Invalid Docker container ID")

            if errors:
                raise ValidationError(errors)

        return True

    @property
    def state(self):
        """
        Current state that the build is in
        """
        if self.result is not None:
            return self.result
        elif self.build_stages:
            return 'running'  # TODO check if running or dead
        else:
            return 'queued'  # TODO check if queued or queue fail

    _docker_client = None

    @property
    def docker_client(self):
        """
        Get the cached (or new) Docker Client object being used for this build

        CACHED VALUES NOT AVAILABLE OUTSIDE FORK
        """
        if not self._docker_client:
            if self.has_value('docker_client_host'):
                docker_client_args = {'base_url': self.docker_client_host}

            elif CONFIG.docker_use_env_vars:
                docker_client_args = kwargs_from_env()

            else:
                docker_client_args = {
                    # TODO real load balancing, queueing
                    'base_url': random.choice(CONFIG.docker_hosts),
                }

            self._docker_client = docker.Client(**docker_client_args)
            self.save()

        return self._docker_client

    @property
    def build_output_details(self):
        """
        Details for build output artifacts
        """
        # pylint:disable=no-member
        output_files = (
            (name, self.build_output_path().join('%s.tar' % name))
            for name in self.build_config.build_output.keys()
        )
        return {
            name: {'size': bytes_human_readable(path.size()),
                   'link': url_for('build_output_view',
                                   job_slug=self.job_slug,
                                   build_slug=self.slug,
                                   filename='%s.tar' % name,
                                   ),
                   }
            for name, path in output_files
            if path.check(file=True)
        }

    @property
    def docker_image_name(self):
        """
        Get the docker image name, including repository where necessary
        """
        if CONFIG.docker_use_registry:
            return '{host}/{name}'.format(host=CONFIG.docker_registry_host,
                                          name=self.job_slug)

        return self.job_slug

    @property
    def docker_full_name(self):
        """
        Get the full name of the docker image, including tag, and repository
        where necessary
        """
        if self.tag:
            return '{name}:{tag}'.format(name=self.docker_image_name,
                                         tag=self.tag)

        return self.docker_image_name

    @property
    def is_stable_release(self):
        """
        Check if this is a successfully run, tagged build
        """
        return self.result == 'success' and self.tag is not None

    def data_file_path(self):
        # Add the job name before the build slug in the path
        data_file_path = super(Build, self).data_file_path()
        return data_file_path.join(
            '..', self.job.slug, data_file_path.basename
        )

    def build_output_path(self):
        """
        Directory for any build output data
        """
        return self.data_file_path().join('..', '%s_output' % self.slug)

    def queue(self):
        """
        Add the build to the queue
        """
        if self.start_ts:
            raise AlreadyRunError(self)

        # TODO fix and reenable pylint check for cyclic-import
        from dockci.workers import run_build_async
        run_build_async(self.job_slug, self.slug)

    def _run_now(self):
        """
        Worker func that performs the build
        """
        self.start_ts = datetime.now()
        self.save()

        try:
            with tempfile.TemporaryDirectory() as workdir:
                workdir = py.path.local(workdir)
                pre_build = (stage() for stage in (
                    lambda: self._stage(
                        WorkdirStage(self, workdir)
                    ).returncode == 0,
                    lambda: self._stage(
                        GitInfoStage(self, workdir)
                    ).returncode == 0,
                    lambda: self._stage(
                        GitChangesStage(self, workdir)
                    ).returncode == 0,
                    lambda: self._stage(
                        TagVersionStage(self, workdir)
                    ),  # RC is ignored
                    lambda: self._stage(
                        ProvisionStage(self)
                    ).returncode == 0,
                    lambda: self._stage(
                        BuildDockerStage(self, workdir)
                    ).returncode == 0,
                ))
                if not all(pre_build):
                    self.result = 'error'
                    return False

                if not self._stage(TestStage(self)).returncode == 0:
                    self.result = 'fail'
                    return False

                # We should fail the build here because if this is a tagged
                # build, we can't rebuild it
                if not self._stage(PushStage(self)).returncode == 0:
                    self.result = 'error'
                    return False

                self.result = 'success'
                self.save()

                # Failing this doesn't indicade build failure
                # TODO what kind of a failure would this not working be?
                self._stage(FetchStage(self))

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'error'
            self._error_stage('error')

            return False

        finally:
            try:
                self._stage(CleanupStage(self))

            except Exception:  # pylint:disable=broad-except
                self._error_stage('cleanup_error')

            self.complete_ts = datetime.now()
            self.save()

    def _error_stage(self, stage_slug):
        """
        Create an error stage and add stack trace for it
        """
        self.build_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()

        import traceback
        try:
            BuildStage(
                self,
                stage_slug,
                lambda handle: handle.write(
                    bytes(traceback.format_exc(), 'utf8')
                )
            ).run()
        except Exception:  # pylint:disable=broad-except
            print(traceback.format_exc())

    def _stage(self, stage):
        """
        Create and save a new build stage, running the given args and saving
        its output
        """

        logging.getLogger('dockci.build.stages').debug(
            "Starting '%s' build stage for build '%s'", stage.slug, self.slug
        )

        self.build_stage_slugs.append(stage.slug)  # pylint:disable=no-member
        self.save()

        stage.run()
        return stage
