"""
DockCI - CI, but with that all important Docker twist
"""

from uuid import uuid4

import py.error  # pylint:disable=import-error

from flask import url_for
from yaml_model import (LoadOnAccess,
                        Model,
                        ModelReference,
                        OnAccess,
                        ValidationError,
                        )

from dockci.models.auth import User
from dockci.server import OAUTH_APPS
from dockci.util import is_yaml_file, is_git_ancestor


def all_projects():
    """
    Get the list of projects
    """
    try:
        for path in Project.data_dir_path().listdir():
            if is_yaml_file(path):
                project = Project(path.purebasename)
                yield project

    except py.error.ENOENT:
        return


class Project(Model):  # pylint:disable=too-few-public-methods
    """
    A project, representing a container to be built
    """
    def __init__(self, slug=None):
        super(Project, self).__init__()
        self.slug = slug

    def delete(self):
        """
        Delete the project, including GitHub hooks, and related job data

        Notes:
          If there's an error deleting GitHub hook, it's ignored
        """
        from dockci.models.job import Job

        try:
            Job.delete_all_in_project(self)
        except py.error.ENOENT:
            pass

        return super(Project, self).delete()

    def _all_jobs(self, reverse_=True):
        """
        Get all the jobs associated with this project
        """
        from dockci.models.job import Job

        try:
            jobs = []

            all_files = Job.data_dir_path().join(self.slug).listdir()
            all_files.sort(reverse=reverse_)

            for filename in all_files:
                if is_yaml_file(filename):
                    jobs.append(Job(project=self,
                                    slug=filename.purebasename))

            return jobs

        except py.error.ENOENT:
            return []

    def latest_job(self, passed=None, versioned=None, other_check=None):
        """
        Find the latest job matching the criteria
        """
        try:
            return next(self.filtered_jobs(passed, versioned, other_check))

        except StopIteration:
            return None

    def filtered_jobs(self, passed=None, versioned=None, other_check=None):
        """
        Generator, filtering jobs matching the criteria
        """
        for job in list(self.jobs):
            # job_passed is used only in this loop iter
            # pylint:disable=cell-var-from-loop
            job_passed = lambda: job.result == 'success'  # lazy load
            if passed is not None and job_passed() != passed:
                continue
            if versioned is not None and job.tag is None:
                continue
            if other_check is not None and not other_check(job):
                continue

            yield job

    def latest_job_ancestor(self,
                            workdir,
                            commit,
                            passed=None,
                            versioned=None):
        """
        Find the latest job, matching the criteria, who's a git ancestor of
        the given commit
        """

        def check_job(job):
            """
            Use git merge-base to check
            """
            return is_git_ancestor(workdir, job.commit, commit)

        return self.latest_job(passed, versioned, check_job)

    def validate(self):
        with self.parent_validation(Project):
            errors = []

            if not self.repo:
                errors.append("Repository can not be blank")
            if not self.name:
                errors.append("Name can not be blank")

            if bool(self.hipchat_api_token) != bool(self.hipchat_room):
                errors.append("Both, or neither HipChat values must be given")

            if errors:
                raise ValidationError(errors)

        return True

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

            self.save()

        return result

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

    slug = None
    repo = LoadOnAccess(default=lambda _: '')
    name = LoadOnAccess(default=lambda _: '')
    # TODO encrypt decrypt sensitive data etc..
    hipchat_api_token = LoadOnAccess(default=lambda _: '')
    hipchat_room = LoadOnAccess(default=lambda _: '')

    github_repo_id = LoadOnAccess(default=lambda _: None)
    github_hook_id = LoadOnAccess(default=lambda _: None)
    github_secret = LoadOnAccess(default=lambda _: None)
    github_auth_user = ModelReference(lambda self: User(
        self.github_auth_user_slug
    ), default=lambda _: None)

    jobs = OnAccess(_all_jobs)
