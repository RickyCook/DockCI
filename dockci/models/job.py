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
import sqlalchemy

from docker.utils import kwargs_from_env
from flask import url_for
from sqlalchemy.sql import func as sql_func

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
                                                   GitMtimeStage,
                                                   ProvisionStage,
                                                   TagVersionStage,
                                                   UtilStage,
                                                   WorkdirStage,
                                                   )
from dockci.server import CONFIG, DB, OAUTH_APPS
from dockci.util import (bytes_human_readable,
                         client_kwargs_from_config,
                         )


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
class Job(DB.Model):
    """ An individual project job, and result """

    id = DB.Column(DB.Integer(), primary_key=True)
    create_ts = DB.Column(
        DB.DateTime(), nullable=False, default=sql_func.now(),
    )
    start_ts = DB.Column(DB.DateTime())
    complete_ts = DB.Column(DB.DateTime())
    result = DB.Column(DB.Enum(
        *JobResult.__members__,
        name='job_results'
    ), index=True)
    repo = DB.Column(DB.Text(), nullable=False)
    commit = DB.Column(DB.String(41), nullable=False)
    tag = DB.Column(DB.Text())
    image_id = DB.Column(DB.String(65))
    container_id = DB.Column(DB.String(65))
    exit_code = DB.Column(DB.Integer())
    docker_client_host = DB.Column(DB.Text())
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

    _provisioned_containers = []

    _job_config = None
    _db_session = None

    def __str__(self):
        return '<{klass}: {project_slug}/{job_slug}>'.format(
            klass=self.__class__.__name__,
            project_slug=self.project.slug,
            job_slug=self.slug,
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
    def docker_image_name(self):
        """
        Get the docker image name, including repository where necessary
        """
        if CONFIG.docker_use_registry:
            return '{host}/{name}'.format(host=CONFIG.docker_registry_host,
                                          name=self.project.slug)

        return self.project.slug

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

    def _run_now(self):
        """
        Worker func that performs the job
        """
        self.start_ts = datetime.now()
        self.db_session.add(self)
        self.db_session.commit()

        try:
            with tempfile.TemporaryDirectory() as workdir:
                workdir = py.path.local(workdir)

                git_info = (stage() for stage in (
                    lambda: WorkdirStage(self, workdir).run(0),
                    lambda: GitInfoStage(self, workdir).run(0),
                ))

                if not all(git_info):
                    self.result = 'broken'
                    return False

                def create_util_stage(suffix, config):
                    """ Create a UtilStage wrapped in lambda for running """
                    return lambda: UtilStage(
                        self, workdir, suffix, config,
                    ).run(0)

                prepare = (stage() for stage in chain(
                    (
                        lambda: GitChangesStage(self, workdir).run(0),
                        lambda: GitMtimeStage(self, workdir).run(None),
                        lambda: TagVersionStage(self, workdir).run(None),
                    ), (
                        create_util_stage(suffix_outer, config_outer)
                        for suffix_outer, config_outer
                        in self.utilities.items()
                    ), (
                        lambda: ProvisionStage(self).run(0),
                        lambda: BuildStage(self, workdir).run(0),
                    )
                ))

                if self.project.github_repo_id:
                    ExternalStatusStage(self, 'start').run(0)

                if not all(prepare):
                    self.result = 'broken'
                    self.db_session.add(self)
                    self.db_session.commit()
                    return False

                if not TestStage(self).run(0):
                    self.result = 'fail'
                    self.db_session.add(self)
                    self.db_session.commit()
                    return False

                # We should fail the job here because if this is a tagged
                # job, we can't rebuild it
                if not PushStage(self).run(0):
                    self.result = 'broken'
                    self.db_session.add(self)
                    self.db_session.commit()
                    return False

                self.result = 'success'
                self.db_session.add(self)
                self.db_session.commit()

                # Failing this doesn't indicate job failure
                # TODO what kind of a failure would this not working be?
                FetchStage(self).run(None)

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'broken'
            self.db_session.add(self)
            self.db_session.commit()
            self._error_stage('error')

            return False

        finally:
            try:
                ExternalStatusStage(self, 'complete').run(0)
                CleanupStage(self).run(None)

            except Exception:  # pylint:disable=broad-except
                self._error_stage('post_error')

            self.complete_ts = datetime.now()
            self.db_session.add(self)
            self.db_session.commit()

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
