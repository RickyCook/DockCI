"""
DockCI - CI, but with that all important Docker twist
"""

import py.error  # pylint:disable=import-error

from yaml_model import LoadOnAccess, Model, OnAccess, ValidationError

from dockci.util import is_yaml_file, is_git_ancestor


def all_jobs():
    """
    Get the list of jobs
    """
    try:
        for path in Job.data_dir_path().listdir():
            if is_yaml_file(path):
                job = Job(path.purebasename)
                yield job

    except py.error.ENOENT:
        return


class Job(Model):  # pylint:disable=too-few-public-methods
    """
    A job, representing a container to be built
    """
    def __init__(self, slug=None):
        super(Job, self).__init__()
        self.slug = slug

    def _all_builds(self, reverse_=True):
        """
        Get all the builds associated with this job
        """
        from dockci.models.build import Build

        try:
            builds = []

            all_files = Build.data_dir_path().join(self.slug).listdir()
            all_files.sort(reverse=reverse_)

            for filename in all_files:
                if is_yaml_file(filename):
                    builds.append(Build(job=self,
                                        slug=filename.purebasename))

            return builds

        except py.error.ENOENT:
            return []

    def latest_build(self, passed=None, versioned=None, other_check=None):
        """
        Find the latest build matching the criteria
        """
        try:
            return next(self.filtered_builds(passed, versioned, other_check))

        except StopIteration:
            return None

    def filtered_builds(self, passed=None, versioned=None, other_check=None):
        """
        Generator, filtering builds matching the criteria
        """
        for build in list(self.builds):
            # build_passed is used only in this loop iter
            # pylint:disable=cell-var-from-loop
            build_passed = lambda: build.result == 'success'  # lazy load
            if passed is not None and build_passed() != passed:
                continue
            if versioned is not None and build.tag is None:
                continue
            if other_check is not None and not other_check(build):
                continue

            yield build

    def latest_build_ancestor(self,
                              workdir,
                              commit,
                              passed=None,
                              versioned=None):
        """
        Find the latest build, matching the criteria, who's a git ancestor of
        the given commit
        """

        def check_build(build):
            """
            Use git merge-base to check
            """
            return is_git_ancestor(workdir, build.commit, commit)

        return self.latest_build(passed, versioned, check_build)

    def validate(self):
        with self.parent_validation(Job):
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

    slug = None
    repo = LoadOnAccess(default=lambda _: '')
    name = LoadOnAccess(default=lambda _: '')
    # TODO encrypt decrypt sensitive data etc..
    hipchat_api_token = LoadOnAccess(default=lambda _: '')
    hipchat_room = LoadOnAccess(default=lambda _: '')
    github_secret = LoadOnAccess(default=lambda _: None)
    builds = OnAccess(_all_builds)
