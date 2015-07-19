"""
Preparation for the main job stages
"""

import glob
import json
import re
import subprocess

from collections import defaultdict
from datetime import datetime
from itertools import chain

import docker
import docker.errors
import py.error  # pylint:disable=import-error

from dockci.models.project import Project
from dockci.models.job_meta.config import JobConfig
from dockci.models.job_meta.stages import JobStageBase, CommandJobStage
from dockci.server import CONFIG
from dockci.util import docker_ensure_image, FauxDockerLog, write_all


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


def recursive_mtime(path, timestamp):
    """
    Recursively set mtime on the given path, returning the number of
    additional files or directories changed
    """
    path.setmtime(timestamp)
    extra = 0
    if path.isdir():
        for subpath in path.visit():
            try:
                subpath.setmtime(timestamp)
                extra += 1
            except py.error.ENOENT:
                pass

    return extra


class GitMtimeStage(JobStageBase):
    """
    Change the modified time to the commit time for any files in an ADD
    directive of a Dockerfile
    """

    slug = 'git_mtime'

    def __init__(self, job, workdir):
        super(GitMtimeStage, self).__init__(job)
        self.workdir = workdir

    def dockerfile_globs(self):
        """ Get all glob patterns from the Dockerfile """
        dockerfile = self.workdir.join('Dockerfile')
        with dockerfile.open() as handle:
            for line in handle:
                if line[:4] == 'ADD ':
                    add_value = line[4:]
                    try:
                        for path in json.loads(add_value)[:-1]:
                            yield path

                    except ValueError:
                        add_file, _ = add_value.split(' ', 1)
                        yield add_file

        yield 'Dockerfile'
        yield '.dockerignore'

    def sorted_dockerfile_globs(self, reverse=False):
        """
        Sorted globs from the Dockerfile. Paths are sorted based on depth
        """
        def keyfunc(glob_str):
            """ Compare paths, ranking higher level dirs lower """
            path = self.workdir.join(glob_str)
            try:
                if path.samefile(self.workdir):
                    return -1
            except py.error.ENOENT:
                pass

            return len(path.parts())
        return sorted(self.dockerfile_globs(), key=keyfunc, reverse=reverse)

    def timestamp_for(self, path):
        """ Get the timestamp for the given path """
        if path.samefile(self.workdir):
            git_cmd = [
                'git', 'log', '-1', '--format=format:%ct',
            ]
        else:
            git_cmd = [
                'git', 'log', '-1', '--format=format:%ct', '--', path.strpath,
            ]

        # Get the timestamp
        return int(subprocess.check_output(
            git_cmd,
            stderr=subprocess.STDOUT,
            cwd=self.workdir.strpath,
        ))

    def path_mtime(self, handle, path):
        """
        Set the mtime on the given path, writitng messages to the file handle
        given as necessary
        """
        # Ensure path is inside workdir
        if not path.common(self.workdir).samefile(self.workdir):
            write_all(handle,
                      "%s not in the workdir; failing" % path.strpath)
            return False

        if not path.check():
            return True

        # Show the file, relative to workdir
        relpath = self.workdir.bestrelpath(path)
        write_all(handle, "%s: " % relpath)

        try:
            timestamp = self.timestamp_for(path)

        except subprocess.CalledProcessError as ex:
            # Something happened with the git command
            write_all(handle, [
                "Could not retrieve commit time from git. Exit "
                "code %d:\n" % ex.returncode,

                ex.output,
            ])
            return False

        except ValueError as ex:
            # A non-int value returned
            write_all(handle,
                      "Unexpected output from git: %s\n" % ex.args[0])
            return False

        # User output
        mtime = datetime.fromtimestamp(timestamp)
        write_all(handle, "%s... " % mtime.strftime('%Y-%m-%d %H:%M:%S'))

        # Set the time!
        extra = recursive_mtime(path, timestamp)

        extra_txt = ("(and %d more) " % extra) if extra > 0 else ""
        handle.write("{}DONE!\n".format(extra_txt).encode())
        if path.samefile(self.workdir):
            write_all(
                handle,
                "** Note: Performance benefits may be gained by adding "
                "only necessary files, rather than the whole source tree "
                "**\n",
            )

        return True

    def runnable(self, handle):
        """ Scrape the Dockerfile, update any ``mtime``s """
        try:
            globs = self.sorted_dockerfile_globs()

        except py.error.ENOENT:
            write_all(handle, "No Dockerfile! Can not continue")
            return 1

        # Join with workdir, unglob, and turn into py.path.local
        all_files = chain(*(
            (
                py.path.local(path)
                for path in glob.iglob(self.workdir.join(repo_glob).strpath)
            )
            for repo_glob in globs
        ))

        success = True
        for path in all_files:
            success &= self.path_mtime(handle, path)

        return 0 if success else 1


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


class InlineProjectStage(JobStageBase):
    """ Stage to run project containers inline in another project job """
    def get_project_slugs(self):
        """ Get all project slugs that relate to this inline stage """
        raise NotImplementedError(
            "You must override the 'get_project_slugs' method"
        )

    def id_for_project(self, project_slug):
        """ Get the event series ID for a given project's slug """
        # pylint:disable=no-member
        return '%s_%s' % (self.slug, project_slug)

    def runnable(self, handle):
        """
        Resolve project containers, and pass control to ``runnable_inline``
        """
        all_okay = True
        faux_log = FauxDockerLog(handle)
        for project_slug in self.get_project_slugs():

            # pylint:disable=no-member
            defaults = {'id': self.id_for_project(project_slug)}
            with faux_log.more_defaults(**defaults):

                defaults = {'status': "Finding service %s" % project_slug}
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

                defaults = {'status': "Pulling container image %s:%s" % (
                    service_job.docker_image_name, service_job.tag
                )}
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

                    except docker.errors.APIError as ex:
                        faux_log.update(error=ex.explanation.decode())
                        all_okay = False
                        continue

                    if image_id is None:
                        faux_log.update(error="Not found")
                        all_okay = False
                        continue

                    faux_log.update(progress="Done")

                all_okay &= self.runnable_inline(
                    service_job,
                    image_id,
                    handle,
                    faux_log,
                )

        return 0 if all_okay else 1

    def runnable_inline(self, service_job, image_id, handle, faux_log):
        """ Executed for each service job """
        raise NotImplementedError(
            "You must override the 'get_project_slugs' method"
        )


class ProvisionStage(InlineProjectStage):
    """
    Provision the services that are required for this job
    """

    slug = 'docker_provision'

    def get_project_slugs(self):
        return set(self.job.job_config.services.keys())

    def runnable_inline(self, service_job, image_id, handle, faux_log):
        service_project = service_job.project
        service_config = self.job.job_config.services[service_project.slug]

        defaults = {'status': "Starting service %s %s" % (
            service_project.name,
            service_job.tag,
        )}
        with faux_log.more_defaults(**defaults):
            faux_log.update()

            service_kwargs = {
                key: value for key, value in service_config.items()
                if key in ('command', 'environment')
            }

            try:
                container = self.job.docker_client.create_container(
                    image=image_id,
                    **service_kwargs
                )
                self.job.docker_client.start(container['Id'])

                # Store the provisioning info
                # pylint:disable=protected-access
                self.job._provisioned_containers.append({
                    'project_slug': service_project.slug,
                    'config': service_config,
                    'id': container['Id']
                })
                faux_log.update(progress="Done")

            except docker.errors.APIError as ex:
                faux_log.update(error=ex.explanation.decode())
                return False

        return True


class UtilStage(InlineProjectStage):
    """ Create, and run a utility stage container """
    def __init__(self, job, slug_suffix, config):
        super(UtilStage, self).__init__(job)
        self.slug = "utility_%s" % slug_suffix
        self.config = config

    def get_project_slugs(self):
        return (self.config['name'],)

    def id_for_project(self, project_slug):
        return project_slug

    def runnable_inline(self, service_job, image_id, handle, faux_log):
        utility_project = service_job.project
        defaults = {'status': "Starting %s utility %s" % (
            utility_project.name,
            service_job.tag,
        )}
        with faux_log.more_defaults(**defaults):
            faux_log.update()

            service_kwargs = {
                key: value for key, value in self.config.items()
                if key in ('command', 'environment')
            }

            try:
                container = self.job.docker_client.create_container(
                    image=image_id,
                    **service_kwargs
                )
                stream = self.job.docker_client.attach(
                    container['Id'],
                    stream=True,
                )
                self.job.docker_client.start(container['Id'])

            except docker.errors.APIError as ex:
                faux_log.update(error=ex.explanation.decode())
                return False

        for line in stream:
            if isinstance(line, bytes):
                handle.write(line)
            else:
                handle.write(line.encode())

            handle.flush()

        defaults = {
            'id': "%s-cleanup" % self.id_for_project(utility_project.slug),
        }
        with faux_log.more_defaults(**defaults):
            faux_log.update(status="Collecting status")
            details = self.job.docker_client.inspect_container(container['Id'])

            defaults = {
                'status': "Cleaning up %s utility" % utility_project.name
            }
            with faux_log.more_defaults(**defaults):
                try:
                    self.job.docker_client.remove_container(
                        container['Id'],
                    )
                    faux_log.update(progress="Done")
                except docker.errors.APIError as ex:
                    faux_log.update(error=ex.explanation.decode())

        if details['State']['ExitCode'] != 0:
            faux_log.update(
                id="%s-exit" % self.id_for_project(utility_project.slug),
                error="Exit code was %d" % (
                    details['State']['ExitCode'],
                )
            )
            return False

        return True

    @classmethod
    def slug_suffixes(cls, utility_names):
        """ See ``slug_suffixes_gen`` """
        return list(cls.slug_suffixes_gen(utility_names))

    @classmethod
    def slug_suffixes_gen(cls, utility_names):
        """
        Generate utility names into unique slug suffixes by adding a counter to
        the end, if there are duplicates
        """
        totals = defaultdict(int)
        for name in utility_names:
            totals[name] += 1

        counters = defaultdict(int)
        for name in utility_names:
            if totals[name] > 1:
                counters[name] += 1
                yield '%s_%d' % (name, counters[name])

            else:
                yield name
