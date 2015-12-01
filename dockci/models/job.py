"""
DockCI - CI, but with that all important Docker twist
"""

import random
import sys
import tempfile

from collections import OrderedDict
from datetime import datetime
from enum import Enum
from itertools import chain

import docker
import py.path  # pylint:disable=import-error
import semver
import sqlalchemy

from docker.utils import kwargs_from_env
from flask import url_for

from .base import RepoFsMixin
from dockci.exceptions import AlreadyRunError, InvalidServiceTypeError
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
                                                   GitMtimeStage,
                                                   TagVersionStage,
                                                   WorkdirStage,
                                                   )
from dockci.models.job_meta.stages_prepare_docker import (DockerLoginStage,
                                                          ProvisionStage,
                                                          PushPrepStage,
                                                          UtilStage,
                                                          )
from dockci.server import CONFIG, DB, OAUTH_APPS
from dockci.util import (add_to_url_path,
                         bytes_human_readable,
                         client_kwargs_from_config,
                         ext_url_for,
                         )


STATE_MAP = {
    'github': {
        'queued': 'pending',
        'running': 'pending',
        'success': 'success',
        'fail': 'failure',
        'broken': 'error',
        None: 'error',
    },
    'gitlab': {
        'queued': 'pending',
        'running': 'running',
        'success': 'success',
        'fail': 'failed',
        'broken': 'canceled',
        None: 'canceled',
    },
}


class JobResult(Enum):
    """ Possible results for Job models """
    success = 'success'
    fail = 'fail'
    broken = 'broken'


class JobStageTmp(DB.Model):  # pylint:disable=no-init
    """ Quick and dirty list of job stages for the time being """
    id = DB.Column(DB.Integer(), primary_key=True)
    slug = DB.Column(DB.String(31))
    job_id = DB.Column(DB.Integer, DB.ForeignKey('job.id'), index=True)
    job = DB.relationship(
        'Job',
        foreign_keys="JobStageTmp.job_id",
        backref=DB.backref(
            'job_stages',
            order_by=sqlalchemy.asc('id'),
            ))


# pylint:disable=too-many-instance-attributes,no-init,too-many-public-methods
class Job(DB.Model, RepoFsMixin):
    """ An individual project job, and result """

    id = DB.Column(DB.Integer(), primary_key=True)

    create_ts = DB.Column(
        DB.DateTime(), nullable=False, default=datetime.now,
    )
    start_ts = DB.Column(DB.DateTime())
    complete_ts = DB.Column(DB.DateTime())

    result = DB.Column(DB.Enum(
        *JobResult.__members__,
        name='job_results'
    ), index=True)
    repo_fs = DB.Column(DB.Text(), nullable=False)
    commit = DB.Column(DB.String(41), nullable=False)
    tag = DB.Column(DB.Text())
    image_id = DB.Column(DB.String(65))
    container_id = DB.Column(DB.String(65))
    exit_code = DB.Column(DB.Integer())
    docker_client_host = DB.Column(DB.Text())
    git_branch = DB.Column(DB.Text())
    git_author_name = DB.Column(DB.Text())
    git_author_email = DB.Column(DB.Text())
    git_committer_name = DB.Column(DB.Text())
    git_committer_email = DB.Column(DB.Text())
    git_changes = DB.Column(DB.Text())

    ancestor_job_id = DB.Column(DB.Integer, DB.ForeignKey('job.id'))
    child_jobs = DB.relationship(
        'Job',
        foreign_keys="Job.ancestor_job_id",
        backref=DB.backref('ancestor_job', remote_side=[id]),
    )
    project_id = DB.Column(DB.Integer, DB.ForeignKey('project.id'), index=True)

    _job_config = None
    _db_session = None

    # Defaults in init_transient
    _provisioned_containers = None
    _old_image_ids = None
    _stage_objects = None

    def __init__(self, *args, **kwargs):
        super(Job, self).__init__(*args, **kwargs)
        self.init_transient()

    @sqlalchemy.orm.reconstructor
    def init_transient(self):
        """ SLQAlchemy doesn't __init__, so need this separately """
        self._provisioned_containers = []
        self._old_image_ids = []
        self._stage_objects = {}

    def __str__(self):
        try:
            slug = self.slug
        except TypeError:
            slug = self.id

        return '<{klass}: {project_slug}/{job_slug}>'.format(
            klass=self.__class__.__name__,
            project_slug=self.project.slug,
            job_slug=slug,
        )

    @property
    def db_session(self):
        """
        DB session for this Job is used in job workers without an application
        context
        """
        if self._db_session is None:
            self._db_session = DB.session()
        return self._db_session

    @property
    def job_config(self):
        """ JobConfig for this Job """
        if self._job_config is None:
            self._job_config = JobConfig(self)
        return self._job_config

    @property
    def slug(self):
        """ Generated web slug for this job """
        return self.slug_from_id(self.id)

    @classmethod
    def id_from_slug(cls, slug):
        """ Convert a slug to an ID for ORM lookup """
        return int(slug, 16)

    @classmethod
    def slug_from_id(cls, id_):
        """ Convert an ID to a slug (padded hex) """
        return '{:0>6}'.format(hex(id_)[2:])

    @property
    def compound_slug(self):
        """
        A slug that includes all identifiers necessary for this model to be
        unique in the data set
        """
        return '%s/%s' % (self.project.slug, self.slug)

    @property
    def url(self):
        """ URL for this job """
        return url_for('job_view',
                       project_slug=self.project.slug,
                       job_slug=self.slug)

    @property
    def url_ext(self):
        """ URL for this project """
        return ext_url_for('job_view',
                           project_slug=self.project.slug,
                           job_slug=self.slug)

    @property
    def github_api_status_endpoint(self):
        """ Status endpoint for GitHub API """
        return '%s/commits/%s/statuses' % (
            self.project.github_api_repo_endpoint,
            self.commit,
        )

    @property
    def gitlab_api_status_endpoint(self):
        """ Status endpoint for GitLab API """
        return add_to_url_path(
            self.project.gitlab_api_repo_endpoint,
            '/statuses/%s' % self.commit,
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

    def changed_result(self, workdir=None):
        """
        Check if this job changed the result from it's ancestor. None if
        there's no result yet
        """
        if self.result is None:
            return None

        ancestor_job = self.ancestor_job
        if not ancestor_job:
            return True

        if ancestor_job.result is None:
            if workdir is None:  # Can't get a better ancestor
                return True

            ancestor_job = self.project.latest_job_ancestor(
                workdir, self.commit, complete=True,
            )

        if not ancestor_job:
            return True

        return ancestor_job.result != self.result

    _docker_client = None

    @property
    def docker_client(self):
        """
        Get the cached (or new) Docker Client object being used for this job

        CACHED VALUES NOT AVAILABLE OUTSIDE FORK
        """
        if self._docker_client is None:
            if self.docker_client_host is not None:
                for host_str in CONFIG.docker_hosts:
                    if host_str.startswith(self.docker_client_host):
                        docker_client_args = client_kwargs_from_config(
                            host_str,
                        )

            elif CONFIG.docker_use_env_vars:
                docker_client_args = kwargs_from_env()

            else:
                docker_client_args = client_kwargs_from_config(
                    # TODO real load balancing, queueing
                    random.choice(CONFIG.docker_hosts),
                )

            self.docker_client_host = docker_client_args['base_url']
            self.db_session.add(self)
            self.db_session.commit()

            self._docker_client = docker.Client(**docker_client_args)

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
                                   project_slug=self.project.slug,
                                   job_slug=self.slug,
                                   filename='%s.tar' % name,
                                   ),
                   }
            for name, path in output_files
            if path.check(file=True)
        }

    @property
    def tag_semver(self):
        """
        Job tag, parsed as semver (or None if no match). Allows a 'v' prefix
        """
        if self.tag is None:
            return None

        try:
            return semver.parse(self._tag_without_v)
        except ValueError:
            pass

    @property
    def tag_semver_str_v(self):
        """ Job commit's tag with v prefix added or None if not semver """
        without = self.tag_semver_str
        if without:
            return "v%s" % without

    @property
    def tag_semver_str(self):
        """
        Job commit's tag with any v prefix dropped or None if not semver
        """
        if self.tag_semver:
            return self._tag_without_v

    @property
    def _tag_without_v(self):
        """ Job commit's tag with any v prefix dropped """
        if self.tag[0] == 'v':
            return self.tag[1:]
        else:
            return self.tag

    @property
    def branch_tag(self):
        """ Docker tag for the git branch """
        if self.git_branch:
            return 'latest-%s' % self.git_branch

    @property
    def docker_tag(self):
        """ Tag for the docker image """
        if not self.push_candidate:
            return None

        if self.tag_push_candidate:
            return self.tag

        return self.branch_tag

    @property
    def tags_set(self):
        """ Set of all tags this job should be known by """
        return {
            tag
            for tag in (
                self.tag,
                self.branch_tag,
            )
            if tag is not None
        }

    @property
    def tag_tags_set(self):
        """ Set of all tags from the git tag this job may be known by """
        return {
            tag
            for tag in (
                self.tag_semver_str,
                self.tag_semver_str_v,
                self.tag,
            )
            if tag is not None
        }

    @property
    def possible_tags_set(self):
        """ Set of all tags this job may be known by """
        branch_tag = self.branch_tag
        tags_set = self.tag_tags_set
        if branch_tag:
            tags_set.add(branch_tag)

        return tags_set

    @property
    def docker_base_name(self):
        """ Name of the Docker image, without tag or registry """
        return self.project.slug

    @property
    def target_registry_base_name(self):
        """ Base name of the target registry, or None """
        if self.project.target_registry:
            return self.project.target_registry.base_name

    @property
    def docker_image_name(self):
        """
        Get the docker image name, including repository where necessary
        """
        if self.project.target_registry:
            return '{host}/{name}'.format(
                host=self.target_registry_base_name,
                name=self.docker_base_name,
            )

        return self.docker_base_name

    @property
    def docker_full_name(self):
        """
        Get the full name of the docker image, including tag, and repository
        where necessary
        """
        tag = self.docker_tag
        if tag:
            return '{name}:{tag}'.format(name=self.docker_image_name,
                                         tag=tag)

        return self.docker_image_name

    @property
    def is_stable_release(self):
        """
        Check if this is a successfully run, tagged job
        """
        return self.result == 'success' and self.tag is not None

    @property
    def utilities(self):
        """ Dictionary of utility slug suffixes and their configuration """
        utility_suffixes = UtilStage.slug_suffixes_gen([
            config['name']  # TODO handle KeyError gracefully
            # pylint:disable=no-member
            for config in self.job_config.utilities
        ])
        utilities = zip(
            # pylint:disable=no-member
            utility_suffixes, self.job_config.utilities
        )
        return OrderedDict(utilities)

    @property
    def is_good_state(self):
        """ Is the job completed, and in a good state (success) """
        return (
            self.result == JobResult.success.value or
            (self.result is None and self.exit_code == 0)
        )

    @property
    def is_bad_state(self):
        """ Is the job completed, and in a bad state (failed, broken) """
        return self.result in (JobResult.fail.value, JobResult.broken.value)

    @property
    def tag_push_candidate(self):
        """ Determines if this job has a tag, and target registry """
        return bool(self.tag and self.project.target_registry)

    @property
    def branch_push_candidate(self):
        """
        Determines if this job has a branch, target registry and the project
        branch pattern matches
        """
        return bool(
            self.git_branch and
            self.project.branch_pattern and
            self.project.target_registry and
            self.project.branch_pattern.match(self.git_branch)
        )

    @property
    def push_candidate(self):
        """ Is the job a push candidate for either tag or branch push """
        return self.tag_push_candidate or self.branch_push_candidate

    @property
    def pushable(self):
        """ Is the job a push candidate, and in a good state """
        return self.push_candidate and self.is_good_state

    @classmethod
    def delete_all_in_project(cls, project):
        """ Delete all jobs and data for the given project """
        cls.data_dir_path_for_project(project).remove(rec=True)

    @classmethod
    def data_dir_path_for_project(cls, project):
        """ Get the path that jobs reside in for the given project """
        return cls.data_dir_path().join(project.slug)

    @classmethod
    def data_dir_path(cls):
        """ Temporary mock for removing YAML model """
        path = py.path.local('data')
        path.ensure(dir=True)
        return path

    def job_output_path(self):
        """ Directory for any job output data """
        return self.data_dir_path_for_project(self.project).join(self.slug)

    def queue(self):
        """
        Add the job to the queue
        """
        if self.start_ts:
            raise AlreadyRunError(self)

        from dockci.server import APP
        APP.worker_queue.put(self.id)

    def _run_now(self, workdir=None):
        """
        Worker func that performs the job
        """
        if workdir is None:
            with tempfile.TemporaryDirectory() as workdir:
                return self._run_now(py.path.local(workdir))

        self.start_ts = datetime.now()
        self.db_session.add(self)
        self.db_session.commit()

        self._stage_objects = {
            stage.slug: stage
            for stage in [
                WorkdirStage(self, workdir),
                GitInfoStage(self, workdir),
                ExternalStatusStage(self, 'start'),
                GitChangesStage(self, workdir),
                GitMtimeStage(self, workdir),
                TagVersionStage(self, workdir),
                PushPrepStage(self),
                DockerLoginStage(self, workdir),
                ProvisionStage(self),
                BuildStage(self, workdir),
                TestStage(self),
                PushStage(self),
                FetchStage(self),
                ExternalStatusStage(self, 'complete'),
                CleanupStage(self),
            ]
        }

        try:
            git_info = (stage() for stage in (
                lambda: self._stage_objects['git_prepare'].run(0),
                lambda: self._stage_objects['git_info'].run(0),
            ))

            if not all(git_info):
                self.result = 'broken'
                return False

            self._stage_objects.update({
                stage.slug: stage
                for stage in [
                    UtilStage(self, workdir, util_suffix, util_config)
                    for util_suffix, util_config
                    in self.utilities.items()
                ]
            })

            def tag_stage():
                """ Runner for ``TagVersionStage`` """
                if self.tag:
                    return True  # Don't override tags
                else:
                    return self._stage_objects['git_tag'].run(None)

            def push_prep_stage():
                """ Runner for ``PushPrepStage`` """
                if self.push_candidate:
                    return self._stage_objects['docker_push_prep'].run(None)
                else:
                    return True  # No prep to do for unpushable

            def util_stage_wrapper(suffix):
                """ Wrap a util stage for running """
                stage = self._stage_objects['utility_%s' % suffix]
                return lambda: stage.run(0)

            prepare = (stage() for stage in chain(
                (
                    lambda: self._stage_objects['git_changes'].run(0),
                    lambda: self._stage_objects['git_mtime'].run(None),
                    tag_stage,
                    push_prep_stage,
                    lambda: self._stage_objects['docker_login'].run(0),
                ), (
                    util_stage_wrapper(util_suffix)
                    for util_suffix
                    in self.utilities.keys()
                ), (
                    lambda: self._stage_objects['docker_provision'].run(0),
                    lambda: self._stage_objects['docker_build'].run(0),
                )
            ))

            if self.project.github_repo_id:  # TODO GitLab
                self._stage_objects['external_status_start'].run(0)

            if not all(prepare):
                self.result = 'broken'
                self.db_session.add(self)
                self.db_session.commit()
                return False

            if not self._stage_objects['docker_test'].run(0):
                self.result = 'fail'
                self.db_session.add(self)
                self.db_session.commit()
                return False

            # We should fail the job here because if this is a tagged
            # job, we can't rebuild it
            if not self._stage_objects['docker_push'].run(0):
                self.result = 'broken'
                self.db_session.add(self)
                self.db_session.commit()
                return False

            self.result = 'success'
            self.db_session.add(self)
            self.db_session.commit()

            # Failing this doesn't indicate job failure
            # TODO what kind of a failure would this not working be?
            self._stage_objects['docker_fetch'].run(None)

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'broken'
            self.db_session.add(self)
            self.db_session.commit()
            self._error_stage('error')

            return False

        finally:
            try:
                self._stage_objects['external_status_complete'].run(0)
                self._stage_objects['cleanup'].run(None)

            except Exception:  # pylint:disable=broad-except
                self._error_stage('post_error')

            self.complete_ts = datetime.now()
            self.db_session.add(self)
            self.db_session.commit()

    def state_data_for(self, service, state=None, state_msg=None):
        """
        Get the mapped state, and associated message for a service.

        To look up state label, first the dict ``STATE_MAP`` is queried for the
        service name. If no value is found, state is kept as is. If the service
        key is found, looks up the state value. If no value is found, the
        ``None`` key is looked up.

        The state message is simply a switch on the original state.
        """
        state = state or self.state
        service_state = state

        try:
            service_state_map = STATE_MAP[service]

        except KeyError:
            pass

        else:
            try:
                service_state = service_state_map[state]

            except KeyError:
                service_state = service_state_map[None]
                state_msg = "is in an unknown state: '%s'" % state

        if state_msg is None:
            if state == 'running':
                state_msg = "is in progress"
            elif state == 'success':
                state_msg = "completed successfully"
            elif state == 'fail':
                state_msg = "completed with failing tests"
            elif state == 'broken':
                state_msg = "failed to complete due to an error"

        if state_msg is not None:
            state_msg = "The DockCI job %s" % state_msg

        return service_state, state_msg

    def send_gitlab_status(self, state=None, state_msg=None, context='push'):
        """ Send the job state to GitLab (see ``send_external_status`` """
        return self.send_external_status(
            'gitlab',
            self.gitlab_api_status_endpoint,
            state=state,
            state_msg=state_msg,
            context=context,
        )

    def send_github_status(self, state=None, state_msg=None, context='push'):
        """ Send the job state to GitHub (see ``send_external_status`` """
        return self.send_external_status(
            'github',
            self.github_api_status_endpoint,
            state=state,
            state_msg=state_msg,
            context=context,
        )

    def send_external_status(self,
                             service,
                             api_endpoint,
                             state=None,
                             state_msg=None,
                             context='push',
                             ):
        """
        Send a state to the service for the commit represented by this job. If
        state not set, is defaulted to something that makes sense, given the
        data in this model
        """
        state, state_msg = self.state_data_for(service, state, state_msg)

        if state_msg is not None:
            extra_dict = dict(description=state_msg)

        token_data = self.project.external_auth_token
        if token_data.service != service:
            raise InvalidServiceTypeError(
                "Project has a '%s' OAuth token, rather than '%s'" % (
                    token_data.service,
                    service,
                ),
            )

        return OAUTH_APPS[service].post(
            api_endpoint,
            dict(state=state,
                 target_url=self.url_ext,
                 context='continuous-integration/dockci/%s' % context,
                 **extra_dict),
            format='json',
            token=(token_data.key, token_data.secret),
        )

    def _error_stage(self, stage_slug):
        """
        Create an error stage and add stack trace for it
        """
        # TODO all this should be in the try/except
        stage = JobStageTmp(job=self, slug=stage_slug)
        self.db_session.add(stage)
        self.db_session.commit()

        message = None
        try:
            _, ex, _ = sys.exc_info()
            if ex.human_str:
                message = str(ex)

        except AttributeError:
            pass

        if message is None:
            import traceback
            message = traceback.format_exc()

        try:
            JobStage(
                self,
                stage_slug,
                lambda handle: handle.write(
                    message.encode()
                )
            ).run()
        except Exception:  # pylint:disable=broad-except
            print(traceback.format_exc())
