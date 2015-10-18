"""
DockCI - CI, but with that all important Docker twist
"""

import logging
import re

from uuid import uuid4

import py.error  # pylint:disable=import-error
import sqlalchemy

from flask import url_for

from dockci.server import DB, OAUTH_APPS
from dockci.util import is_git_ancestor


DOCKER_REPO_RE = re.compile(r'[a-z0-9-_.]+')


class Project(DB.Model):  # pylint:disable=no-init
    """
    A project, representing a container to be built
    """

    id = DB.Column(DB.Integer(), primary_key=True)
    slug = DB.Column(DB.String(255), unique=True, nullable=False, index=True)
    repo = DB.Column(DB.String(255), nullable=False)
    name = DB.Column(DB.String(255), nullable=False)
    utility = DB.Column(DB.Boolean(), nullable=False, index=True)

    # TODO encrypt decrypt sensitive data etc..
    hipchat_api_token = DB.Column(DB.String(255))
    hipchat_room = DB.Column(DB.String(255))

    # TODO repo ID from repo
    github_repo_id = DB.Column(DB.String(255))
    github_hook_id = DB.Column(DB.Integer())
    github_secret = DB.Column(DB.String(255))
    github_auth_token_id = DB.Column(
        DB.Integer, DB.ForeignKey('o_auth_token.id'),
    )
    github_auth_token = DB.relationship(
        'OAuthToken',
        foreign_keys="Project.github_auth_token_id",
        backref=DB.backref('projects', lazy='dynamic'),
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

        from dockci.models.job import Job

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

    def latest_job(self, passed=None, versioned=None, completed=None):
        """
        Find the latest job matching the criteria
        """
        return self.filtered_jobs(
            passed=passed,
            versioned=versioned,
            completed=completed,
        ).first()

    def filtered_jobs(self, passed=None, versioned=None, completed=True):
        """
        Generator, filtering jobs matching the criteria
        """
        from .job import Job

        query = self.jobs.order_by(sqlalchemy.desc(Job.create_ts))

        def filter_on_value(query, equal, field, value):
            """
            Filter the query for field on a given value being equal, or
            non-equal
            """
            if equal:
                return query.filter(field == value)
            else:
                return query.filter(field != value)

        if passed is not None:
            query = filter_on_value(query, passed, Job.result, 'success')

        if versioned is not None:
            query = filter_on_value(query, not versioned, Job.tag, None)

        if completed is not None:
            query = query.filter(Job.result.in_(('success', 'fail', 'broken')))

        return query

    def latest_job_ancestor(self,
                            workdir,
                            commit,
                            passed=None,
                            versioned=None,
                            completed=None):
        """
        Find the latest job, matching the criteria, who's a git ancestor of
        the given commit
        """

        jobs_query = self.filtered_jobs(
            passed=passed,
            versioned=versioned,
            completed=completed,
        )
        for job in jobs_query:
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
        return url_for('job_new_view',
                       project_slug=self.slug,
                       _external=True)
