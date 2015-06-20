"""
DockCI - CI, but with that all important Docker twist
"""

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
from dockci.models.job_meta.config import JobConfig
from dockci.models.job_meta.stages import JobStage
from dockci.models.job_meta.stages_main import (BuildStage,
                                                ExternalStatusStage,
                                                TestStage,
                                                )
from dockci.models.job_meta.stages_post import (PushStage,
                                                FetchStage,
                                                CleanupStage,
                                                )
from dockci.models.job_meta.stages_prepare import (GitChangesStage,
                                                   GitInfoStage,
                                                   ProvisionStage,
                                                   TagVersionStage,
                                                   WorkdirStage,
                                                   )
from dockci.models.project import Project
from dockci.server import CONFIG, OAUTH_APPS
from dockci.util import bytes_human_readable, is_docker_id


class Job(Model):  # pylint:disable=too-many-instance-attributes
    """
    An individual project job, and result
    """
    def __init__(self, project=None, slug=None):
        super(Job, self).__init__()

        assert project is not None, "Project is given"

        self.project = project
        self.project_slug = project.slug

        if slug:
            self.slug = slug

    slug = OnAccess(lambda _: hex(int(datetime.now().timestamp() * 10000))[2:])
    project = OnAccess(lambda self: Project(self.project_slug))
    project_slug = OnAccess(lambda self: self.project.slug)  # TODO inf. loop
    ancestor_job = ModelReference(lambda self: Job(
        self.project,
        self.ancestor_job_slug
    ), default=lambda _: None)
    create_ts = LoadOnAccess(generate=lambda _: datetime.now())
    start_ts = LoadOnAccess(default=lambda _: None)
    complete_ts = LoadOnAccess(default=lambda _: None)
    result = LoadOnAccess(default=lambda _: None)
    repo = LoadOnAccess(generate=lambda self: self.project.repo)
    commit = LoadOnAccess(default=lambda _: None)
    tag = LoadOnAccess(default=lambda _: None)
    image_id = LoadOnAccess(default=lambda _: None)
    container_id = LoadOnAccess(default=lambda _: None)
    exit_code = LoadOnAccess(default=lambda _: None)
    docker_client_host = LoadOnAccess(
        generate=lambda self: self.docker_client.base_url,
    )
    job_stage_slugs = LoadOnAccess(generate=lambda _: [])
    job_stages = OnAccess(lambda self: [
        JobStage(job=self, slug=slug)
        for slug
        in self.job_stage_slugs
    ])
    git_author_name = LoadOnAccess(default=lambda _: None)
    git_author_email = LoadOnAccess(default=lambda _: None)
    git_committer_name = LoadOnAccess(default=lambda self:
                                      self.git_author_name)
    git_committer_email = LoadOnAccess(default=lambda self:
                                       self.git_author_email)
    git_changes = LoadOnAccess(default=lambda _: None)
    # pylint:disable=unnecessary-lambda
    job_config = OnAccess(lambda self: JobConfig(self))

    _provisioned_containers = []

    def validate(self):
        with self.parent_validation(Job):
            errors = []

            if not self.project:
                errors.append("Parent project not given")
            if self.image_id and not is_docker_id(self.image_id):
                errors.append("Invalid Docker image ID")
            if self.container_id and not is_docker_id(self.container_id):
                errors.append("Invalid Docker container ID")

            if errors:
                raise ValidationError(errors)

        return True

    @property
    def url(self):
        """ URL for this job """
        return url_for('job_view',
                       project_slug=self.project.slug,
                       job_slug=self.slug)

    @property
    def url_ext(self):
        """ URL for this project """
        return url_for('job_view',
                       project_slug=self.project.slug,
                       job_slug=self.slug,
                       _external=True)

    @property
    def github_api_status_endpoint(self):
        """ Status endpoint for GitHub API """
        return '%s/commits/%s/statuses' % (
            self.project.github_api_repo_endpoint,
            self.commit,
        )

    @property
    def state(self):
        """
        Current state that the job is in
        """
        if self.result is not None:
            return self.result
        elif self.job_stages:
            return 'running'  # TODO check if running or dead
        else:
            return 'queued'  # TODO check if queued or queue fail

    _docker_client = None

    @property
    def docker_client(self):
        """
        Get the cached (or new) Docker Client object being used for this job

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
    def job_output_details(self):
        """
        Details for job output artifacts
        """
        # pylint:disable=no-member
        output_files = (
            (name, self.job_output_path().join('%s.tar' % name))
            for name in self.job_config.job_output.keys()
        )
        return {
            name: {'size': bytes_human_readable(path.size()),
                   'link': url_for('job_output_view',
                                   project_slug=self.project_slug,
                                   job_slug=self.slug,
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
                                          name=self.project_slug)

        return self.project_slug

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
        Check if this is a successfully run, tagged job
        """
        return self.result == 'success' and self.tag is not None

    def data_file_path(self):
        # Add the project name before the job slug in the path
        data_file_path = super(Job, self).data_file_path()
        return data_file_path.join(
            '..', self.project.slug, data_file_path.basename
        )

    def job_output_path(self):
        """
        Directory for any job output data
        """
        return self.data_file_path().join('..', '%s_output' % self.slug)

    def queue(self):
        """
        Add the job to the queue
        """
        if self.start_ts:
            raise AlreadyRunError(self)

        # TODO fix and reenable pylint check for cyclic-import
        from dockci.workers import run_job_async
        run_job_async(self.project_slug, self.slug)

    def _run_now(self):
        """
        Worker func that performs the job
        """
        self.start_ts = datetime.now()
        self.save()

        try:
            with tempfile.TemporaryDirectory() as workdir:
                workdir = py.path.local(workdir)

                git_info = (stage() for stage in (
                    lambda: WorkdirStage(self, workdir).run(0),
                    lambda: GitInfoStage(self, workdir).run(0),
                ))

                prepare = (stage() for stage in (
                    lambda: GitChangesStage(self, workdir).run(0),
                    lambda: TagVersionStage(self, workdir).run(None),
                    lambda: ProvisionStage(self).run(0),
                    lambda: BuildStage(self, workdir).run(0),
                ))
                if not all(git_info):
                    self.result = 'broken'
                    return False

                if self.project.github_repo_id:
                    ExternalStatusStage(self, 'start').run(0)

                if not all(prepare):
                    self.result = 'broken'
                    return False

                if not TestStage(self).run(0):
                    self.result = 'fail'
                    return False

                # We should fail the job here because if this is a tagged
                # job, we can't rebuild it
                if not PushStage(self).run(0):
                    self.result = 'broken'
                    return False

                self.result = 'success'
                self.save()

                # Failing this doesn't indicate job failure
                # TODO what kind of a failure would this not working be?
                FetchStage(self).run(None)

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'broken'
            self._error_stage('error')

            return False

        finally:
            try:
                ExternalStatusStage(self, 'complete').run(0)
                CleanupStage(self).run(None)

            except Exception:  # pylint:disable=broad-except
                self._error_stage('post_error')

            self.complete_ts = datetime.now()
            self.save()

    def send_github_status(self, state=None, state_msg=None, context='push'):
        """
        Send a state to the GitHub commit represented by this job. If state
        not set, is defaulted to something that makes sense, given the data in
        this model
        """

        if state is None:
            if self.state == 'running':
                state = 'pending'
            elif self.state == 'success':
                state = 'success'
            elif self.state == 'fail':
                state = 'failure'
            elif self.state == 'broken':
                state = 'broken'
            else:
                state = 'broken'
                state_msg = "is in an unknown state: '%s'" % state

        if state_msg is None:
            if state == 'pending':
                state_msg = "is in progress"
            elif state == 'success':
                state_msg = "completed successfully"
            elif state == 'fail':
                state_msg = "completed with failing tests"
            elif state == 'broken':
                state_msg = "failed to complete due to an error"

        if state_msg is not None:
            extra_dict = dict(description="The DockCI job %s" % state_msg)

        token_data = self.project.github_auth_user.oauth_tokens['github']
        return OAUTH_APPS['github'].post(
            self.github_api_status_endpoint,
            dict(state=state,
                 target_url=self.url_ext,
                 context='continuous-integration/dockci/%s' % context,
                 **extra_dict),
            format='json',
            token=(token_data['key'], token_data['secret']),
        )

    def _error_stage(self, stage_slug):
        """
        Create an error stage and add stack trace for it
        """
        self.job_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()

        import traceback
        try:
            JobStage(
                self,
                stage_slug,
                lambda handle: handle.write(
                    bytes(traceback.format_exc(), 'utf8')
                )
            ).run()
        except Exception:  # pylint:disable=broad-except
            print(traceback.format_exc())
