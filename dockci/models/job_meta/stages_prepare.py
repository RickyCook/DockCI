"""
Preparation for the main job stages
"""

import re
import subprocess

import docker
import docker.errors

from dockci.models.project import Project
from dockci.models.job_meta.config import JobConfig
from dockci.models.job_meta.stages import JobStageBase, CommandJobStage
from dockci.server import CONFIG
from dockci.util import docker_ensure_image, FauxDockerLog


class WorkdirStage(CommandJobStage):
    """ Prepare the working directory """

    slug = 'git_prepare'

    def __init__(self, job, workdir):
        super(WorkdirStage, self).__init__(
            job, workdir, (
                ['git', 'clone', job.repo, workdir.strpath],
                ['git',
                 '-c', 'advice.detachedHead=false',
                 'checkout', job.commit
                 ],
            )
        )

    def runnable(self, handle):
        """
        Clone and checkout the job
        """
        result = super(WorkdirStage, self).runnable(handle)

        # check for, and load job config
        job_config_file = self.workdir.join(JobConfig.slug)
        if job_config_file.check(file=True):
            # pylint:disable=no-member
            self.job.job_config.load(data_file=job_config_file)
            self.job.job_config.save()

        return result


class GitInfoStage(JobStageBase):
    """ Fill the Job with information obtained from the git repo """

    slug = 'git_info'

    def __init__(self, job, workdir):
        super(GitInfoStage, self).__init__(job)
        self.workdir = workdir

    def runnable(self, handle):
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
                                    cwd=self.workdir.strpath,
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
            'Full SHA-1 hash': ('commit', '%H'),
        }
        for display_name, (attr_name, format_string) in properties.items():
            proc = run_proc('git', 'show',
                            '-s',
                            '--format=format:%s' % format_string,
                            'HEAD')

            largest_returncode = max(largest_returncode, proc.returncode)
            value = proc.stdout.read().decode().strip()

            if value != '' and proc.returncode == 0:
                setattr(self.job, attr_name, value)
                properties_empty = False
                handle.write((
                    "%s is %s\n" % (display_name, value)
                ).encode())

        ancestor_job = self.job.project.latest_job_ancestor(
            self.workdir,
            self.job.commit,
        )
        if ancestor_job:
            properties_empty = False
            handle.write((
                "Ancestor job is %s\n" % ancestor_job.slug
            ).encode())
            self.job.ancestor_job = ancestor_job

        if properties_empty:
            handle.write("No information about the git commit could be "
                         "derived\n".encode())

        else:
            self.job.save()

        return proc.returncode


class GitChangesStage(CommandJobStage):
    """
    Get a list of changes from git between now and the most recently built
    ancestor
    """

    slug = 'git_changes'

    def __init__(self, job, workdir):
        cmd_args = []
        if job.has_value('ancestor_job'):
            revision_range_string = '%s..%s' % (
                job.ancestor_job.commit,  # pylint:disable=no-member
                job.commit,
            )

            cmd_args = [
                'git',
                '-c', 'color.ui=always',
                'log', revision_range_string
            ]
        super(GitChangesStage, self).__init__(job, workdir, cmd_args)

    def runnable(self, handle):
        # TODO fix YAML model to return None rather than an empty model so that
        #      if self.ancestor_job will work
        if self.cmd_args:
            return super(GitChangesStage, self).runnable(handle)

        return True


class TagVersionStage(CommandJobStage):
    """
    Try and add a version to the job, based on git tag
    """

    slug = 'git_tag'
    tag_re = re.compile(r'[a-z0-9_.]')

    def __init__(self, job, workdir):
        super(TagVersionStage, self).__init__(
            job, workdir,
            ['git', 'describe', '--tags', '--exact-match'],
        )

    def runnable(self, out_handle):
        returncode = super(TagVersionStage, self).runnable(out_handle)
        if returncode != 0:
            return returncode

        try:
            # TODO opening file to get this is kinda awful
            with self.data_file_path().open() as in_handle:
                last_line = None
                for line in in_handle:
                    line = line.strip()
                    if line and self.tag_re.match(line):
                        last_line = line

                if last_line:
                    self.job.tag = last_line
                    self.job.save()

        except KeyError:
            pass

        return returncode


class ProvisionStage(JobStageBase):
    """
    Provision the services that are required for this job
    """

    slug = 'docker_provision'

    def runnable(self, handle):
        """
        Resolve projects and start services
        """
        all_okay = True
        # pylint:disable=no-member
        services = self.job.job_config.services
        for project_slug, service_config in services.items():
            faux_log = FauxDockerLog(handle)

            defaults = {'status': "Finding service %s" % project_slug,
                        'id': 'docker_provision_%s' % project_slug}
            with faux_log.more_defaults(**defaults):
                faux_log.update()

                service_project = Project(project_slug)
                if not service_project.exists():
                    faux_log.update(error="No project found")
                    all_okay = False
                    continue

                service_job = service_project.latest_job(passed=True,
                                                         versioned=True)
                if not service_job:
                    faux_log.update(
                        error="No successful, versioned job for %s" % (
                            service_project.name
                        ),
                    )
                    all_okay = False
                    continue

            defaults = {
                'status': "Starting service %s %s" % (
                    service_project.name,
                    service_job.tag,
                ),
                'id': 'docker_provision_%s' % project_slug,
            }
            with faux_log.more_defaults(**defaults):
                faux_log.update()

                try:
                    image_id = docker_ensure_image(
                        self.job.docker_client,
                        service_job.image_id,
                        service_job.docker_image_name,
                        service_job.tag,
                        insecure_registry=CONFIG.docker_registry_insecure,
                        handle=handle,
                    )
                    service_kwargs = {
                        key: value for key, value in service_config.items()
                        if key in ('command', 'environment')
                    }
                    container = self.job.docker_client.create_container(
                        image=image_id,
                        **service_kwargs
                    )
                    self.job.docker_client.start(container['Id'])

                    # Store the provisioning info
                    # pylint:disable=protected-access
                    self.job._provisioned_containers.append({
                        'project_slug': project_slug,
                        'config': service_config,
                        'id': container['Id']
                    })
                    faux_log.update(progress="Done")

                except docker.errors.APIError as ex:
                    faux_log.update(error=ex.explanation.decode())
                    all_okay = False

        return 0 if all_okay else 1
