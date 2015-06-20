"""
Preparation for the main build stages
"""

import re
import subprocess

import docker
import docker.errors

from dockci.models.project import Project
from dockci.models.build_meta.config import BuildConfig
from dockci.models.build_meta.stages import BuildStageBase, CommandBuildStage
from dockci.server import CONFIG
from dockci.util import docker_ensure_image, FauxDockerLog


class WorkdirStage(CommandBuildStage):
    """ Prepare the working directory """

    slug = 'git_prepare'

    def __init__(self, build, workdir):
        super(WorkdirStage, self).__init__(
            build, workdir, (
                ['git', 'clone', build.repo, workdir.strpath],
                ['git',
                 '-c', 'advice.detachedHead=false',
                 'checkout', build.commit
                 ],
            )
        )

    def runnable(self, handle):
        """
        Clone and checkout the build
        """
        result = super(WorkdirStage, self).runnable(handle)

        # check for, and load build config
        build_config_file = self.workdir.join(BuildConfig.slug)
        if build_config_file.check(file=True):
            # pylint:disable=no-member
            self.build.build_config.load(data_file=build_config_file)
            self.build.build_config.save()

        return result


class GitInfoStage(BuildStageBase):
    """ Fill the Build with information obtained from the git repo """

    slug = 'git_info'

    def __init__(self, build, workdir):
        super(GitInfoStage, self).__init__(build)
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
                setattr(self.build, attr_name, value)
                properties_empty = False
                handle.write((
                    "%s is %s\n" % (display_name, value)
                ).encode())

        ancestor_build = self.build.project.latest_build_ancestor(
            self.workdir,
            self.build.commit,
        )
        if ancestor_build:
            properties_empty = False
            handle.write((
                "Ancestor build is %s\n" % ancestor_build.slug
            ).encode())
            self.build.ancestor_build = ancestor_build

        if properties_empty:
            handle.write("No information about the git commit could be "
                         "derived\n".encode())

        else:
            self.build.save()

        return proc.returncode


class GitChangesStage(CommandBuildStage):
    """
    Get a list of changes from git between now and the most recently built
    ancestor
    """

    slug = 'git_changes'

    def __init__(self, build, workdir):
        cmd_args = []
        if build.has_value('ancestor_build'):
            revision_range_string = '%s..%s' % (
                build.ancestor_build.commit,  # pylint:disable=no-member
                build.commit,
            )

            cmd_args = [
                'git',
                '-c', 'color.ui=always',
                'log', revision_range_string
            ]
        super(GitChangesStage, self).__init__(build, workdir, cmd_args)

    def runnable(self, handle):
        # TODO fix YAML model to return None rather than an empty model so that
        #      if self.ancestor_build will work
        if self.cmd_args:
            return super(GitChangesStage, self).runnable(handle)

        return True


class TagVersionStage(CommandBuildStage):
    """
    Try and add a version to the build, based on git tag
    """

    slug = 'git_tag'
    tag_re = re.compile(r'[a-z0-9_.]')

    def __init__(self, build, workdir):
        super(TagVersionStage, self).__init__(
            build, workdir,
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
                    self.build.tag = last_line
                    self.build.save()

        except KeyError:
            pass

        return returncode


class ProvisionStage(BuildStageBase):
    """
    Provision the services that are required for this build
    """

    slug = 'docker_provision'

    def runnable(self, handle):
        """
        Resolve projects and start services
        """
        all_okay = True
        # pylint:disable=no-member
        services = self.build.build_config.services
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

                service_build = service_project.latest_build(passed=True,
                                                             versioned=True)
                if not service_build:
                    faux_log.update(
                        error="No successful, versioned build for %s" % (
                            service_project.name
                        ),
                    )
                    all_okay = False
                    continue

            defaults = {
                'status': "Starting service %s %s" % (
                    service_project.name,
                    service_build.tag,
                ),
                'id': 'docker_provision_%s' % project_slug,
            }
            with faux_log.more_defaults(**defaults):
                faux_log.update()

                try:
                    image_id = docker_ensure_image(
                        self.build.docker_client,
                        service_build.image_id,
                        service_build.docker_image_name,
                        service_build.tag,
                        insecure_registry=CONFIG.docker_registry_insecure,
                        handle=handle,
                    )
                    service_kwargs = {
                        key: value for key, value in service_config.items()
                        if key in ('command', 'environment')
                    }
                    container = self.build.docker_client.create_container(
                        image=image_id,
                        **service_kwargs
                    )
                    self.build.docker_client.start(container['Id'])

                    # Store the provisioning info
                    # pylint:disable=protected-access
                    self.build._provisioned_containers.append({
                        'project_slug': project_slug,
                        'config': service_config,
                        'id': container['Id']
                    })
                    faux_log.update(progress="Done")

                except docker.errors.APIError as ex:
                    faux_log.update(error=ex.explanation.decode())
                    all_okay = False

        return 0 if all_okay else 1
