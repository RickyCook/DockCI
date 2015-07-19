"""
Job metadata stored along side the code
"""

from yaml_model import LoadOnAccess, Model, OnAccess


def get_job(slug):
    """
    Wrapper to import, and return a Job object for the JobConfig to avoid
    cyclic import
    """
    from dockci.models.job import Job
    return Job(slug)


class JobConfig(Model):  # pylint:disable=too-few-public-methods
    """
    Job config, loaded from the repo
    """

    slug = 'dockci.yaml'

    job = OnAccess(lambda self: get_job(self.job_slug))
    job_slug = OnAccess(lambda self: self.job.slug)  # TODO infinite loop

    job_output = LoadOnAccess(default=lambda _: {})
    services = LoadOnAccess(default=lambda _: {})
    utilities = LoadOnAccess(default=lambda _: [])

    skip_tests = LoadOnAccess(default=False)

    def __init__(self, job):
        super(JobConfig, self).__init__()

        assert job is not None, "Job is given"

        self.job = job
        self.job_slug = job.slug

    def data_file_path(self):
        # Our data file path is <job output>/<slug>
        return self.job.job_output_path().join(JobConfig.slug)
