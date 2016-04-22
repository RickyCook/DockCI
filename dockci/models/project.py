"""
DockCI - CI, but with that all important Docker twist
"""

import logging
import re

from urllib.parse import quote_plus, urlparse, urlunparse
from uuid import uuid4

import py.error  # pylint:disable=import-error
import sqlalchemy

from flask import url_for

from .base import RepoFsMixin
from .db_types import RegexType
from dockci.server import CONFIG, DB
from dockci.util import (ext_url_for,
                         is_git_ancestor,
                         is_git_hash,
                         )


DOCKER_REPO_RE = re.compile(r'[a-z0-9-_.]+')


class Project(DB.Model, RepoFsMixin):  # pylint:disable=no-init
    """
    A project, representing a container to be built
    """

    id = DB.Column(DB.Integer(), primary_key=True)
    slug = DB.Column(DB.String(255), unique=True, nullable=False, index=True)
    repo = DB.Column(DB.String(255), nullable=False)
    name = DB.Column(DB.String(255), nullable=False)
    branch_pattern = DB.Column(RegexType(), nullable=True)
    utility = DB.Column(DB.Boolean(), nullable=False, index=True)

    # TODO repo ID from repo
    github_repo_id = DB.Column(DB.String(255))
    github_hook_id = DB.Column(DB.Integer())
    github_secret = DB.Column(DB.String(255))

    gitlab_repo_id = DB.Column(DB.String(255))

    external_auth_token_id = DB.Column(
        DB.Integer, DB.ForeignKey('o_auth_token.id'),
    )
    external_auth_token = DB.relationship(
        'OAuthToken',
        foreign_keys="Project.external_auth_token_id",
        backref=DB.backref('projects', lazy='dynamic'),
    )

    target_registry_id = DB.Column(
        DB.Integer, DB.ForeignKey('authenticated_registry.id'),
    )
    target_registry = DB.relationship(
        'AuthenticatedRegistry',
        foreign_keys="Project.target_registry_id",
        backref=DB.backref('target_for', lazy='dynamic'),
    )

    jobs = DB.relationship(
        'Job',
        foreign_keys='Job.project_id',
        cascade='all,delete-orphan',
        backref='project',
        lazy='dynamic',
    )

    def __str__(self):
        return '<{klass}: {project_slug}>'.format(
            klass=self.__class__.__name__,
            project_slug=self.slug,
        )

    def purge(self):
        """
        Delete the project, including GitHub hooks, and related job data

        Notes:
          If there's an error deleting GitHub hook, it's ignored
        """
        DB.session.delete(self)  # No commit just yet

        from .job import Job

        try:
            result = self.delete_github_webhook(save=False)
        except ValueError:
            pass
        else:
            # TODO flash to the user
            logging.error(result.data.get(
                'message',
                "Unexpected response from GitHub. HTTP status %d" % (
                    result.status,
                )
            ))

        try:
            Job.delete_all_in_project(self)
        except py.error.ENOENT:
            pass

        DB.session.commit()

    def latest_job(self,
                   passed=None,
                   versioned=None,
                   tag=None,
                   completed=None,
                   branch=None,
                   ):
        """
        Find the latest job matching the criteria
        """
        from .job import Job
        return Job.filtered_query(
            query=self.jobs.order_by(sqlalchemy.desc(Job.create_ts)),
            passed=passed,
            versioned=versioned,
            tag=tag,
            completed=completed,
            branch=branch,
        ).first()

    def latest_job_ancestor(self,
                            workdir,
                            commit,
                            passed=None,
                            versioned=None,
                            tag=None,
                            completed=None,
                            branch=None,
                            ):
        """
        Find the latest job, matching the criteria, who's a git ancestor of
        the given commit
        """
        from .job import Job

        jobs_query = Job.filtered_query(
            query=self.jobs.order_by(sqlalchemy.desc(Job.create_ts)),
            passed=passed,
            versioned=versioned,
            tag=tag,
            completed=completed,
            branch=branch,
        )
        for job in jobs_query:
            if not is_git_hash(job.commit):  # Skip things like 'HEAD'
                continue

            if is_git_ancestor(workdir, job.commit, commit):
                return job

    def add_github_webhook(self):
        """
        Utility to add a GitHub web hook
        """
        secret = uuid4().hex
        self.github_secret = secret
        endpoint = '%s/hooks' % self.github_api_repo_endpoint
        result = OAUTH_APPS['github'].post(endpoint, {
            'name': 'web',
            'events': ['push'],
            'active': True,
            'config': {
                'url': self.job_new_url_ext,
                'secret': secret,
                'content_type': 'json',
                'insecure_ssl': '0',
            },
        }, format='json')

        if result.status == 201:
            try:
                self.github_hook_id = result.data['id']

            except KeyError:
                pass

            DB.session.add(self)
            DB.session.commit()

        return result

    def delete_github_webhook(self, save=True):
        """ Utility to delete the associated GitHub web hook """
        result = OAUTH_APPS['github'].delete(
            self.github_api_hook_endpoint,
            format='json',
        )

        if result.status == 204:
            self.github_hook_id = None

            if save:
                DB.session.add(self)

        return result

    def is_type(self, service):
        """ Check if the project is of a given service type """
        return (
            getattr(self, '%s_repo_id' % service) and
            self.external_auth_token and
            self.external_auth_token.provider == service
        )

    @property
    def status(self):
        """ Status of the last job for this project """
        latest_completed_job = self.latest_job(completed=True)
        if latest_completed_job is not None:
            return latest_completed_job.result

        else:
            return None

    @property
    def shield_text(self):
        """ Status of this project to show on shields.io shields """
        status = self.status
        if status == 'success':
            return "Passing"
        elif status == 'fail':
            return "Failing"
        elif status is None:
            return "Not Run"

        return status.title()

    @property
    def shield_color(self):
        """ Color for shields.io status shield of this project """
        status = self.status
        if status == 'success':
            return 'green'
        elif status in ('fail', 'broken'):
            return 'red'
        else:
            return 'lightgrey'

    @property
    def gitlab_api_repo_endpoint(self):
        """ Repo endpoint for GitLab API """
        if self.gitlab_repo_id is None:
            raise ValueError("Not a GitLab repository")

        return 'v3/projects/%s' % quote_plus(self.gitlab_repo_id)

    @property
    def github_api_repo_endpoint(self):
        """ Repo endpoint for GitHub API """
        if self.github_repo_id is None:
            raise ValueError("Not a GitHub repository")

        return '/repos/%s' % self.github_repo_id

    @property
    def github_api_hook_endpoint(self):
        """ Hook endpoint for GitHub API """
        if self.github_hook_id is None:
            raise ValueError("GitHub hook not tracked")

        return '%s/hooks/%s' % (self.github_api_repo_endpoint,
                                self.github_hook_id)

    @property
    def url(self):
        """ URL for this project """
        return url_for('project_view', slug=self.slug)

    @property
    def job_new_url(self):
        """ URL for this project """
        return url_for('job_new_view', project_slug=self.slug)

    @property
    def job_new_url_ext(self):
        """ URL for this project """
        return ext_url_for('job_new_view',
                           project_slug=self.slug)

    @property
    def repo_fs(self):
        """ Format string for the repo """
        if self.is_type('gitlab'):
            gitlab_parts = list(urlparse(CONFIG.gitlab_base_url))
            gitlab_parts[1] = 'oauth2:{token_key}@%s' % gitlab_parts[1]
            gitlab_parts[2] = '%s.git' % self.gitlab_repo_id
            return urlunparse(gitlab_parts)

        elif self.is_type('github'):
            return 'https://oauth2:{token_key}@github.com/%s.git' % (
                self.github_repo_id
            )

        return self.repo

    @classmethod
    def get_last_jobs(cls, project_filters=None):
        """ Retrieve the last jobs for all projects """
        if project_filters is None:
            project_filters = {}

        from .job import Job
        job_left = Job
        job_right = sqlalchemy.orm.aliased(Job)

        return job_left.query.outerjoin(
            job_right,
            sqlalchemy.and_(
                job_left.project_id == job_right.project_id,
                job_left.id < job_right.id,
            )
        ).filter(
            job_right.id == None,  # noqa
            Job.project.has(**project_filters)
        )

    @classmethod
    def get_status_summary(cls, project_filters=None):
        """ Retrieve sums of projects in all statuses """
        summary = {'success': 0, 'fail': 0, 'broken': 0, None: 0}
        for job in cls.get_last_jobs(project_filters).all():
            summary[job.result] += 1

        summary['incomplete'] = summary.pop(None)

        return summary
