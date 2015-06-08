"""
DockCI - CI, but with that all important Docker twist
"""

import json
import logging
import random
import re
import tempfile

from datetime import datetime

import docker
import docker.errors
import py.path  # pylint:disable=import-error

from docker.utils import kwargs_from_env
from flask import url_for
from yaml_model import (LoadOnAccess,
                        Model,
                        ModelReference,
                        OnAccess,
                        ValidationError,
                        )

from dockci.exceptions import AlreadyBuiltError, AlreadyRunError
from dockci.models.build_meta.config import BuildConfig
from dockci.models.build_meta.stages import BuildStage, BuildStageBase
from dockci.models.build_meta.stages_prepare import WorkdirStage, GitInfoStage
from dockci.models.job import Job
# TODO fix and reenable pylint check for cyclic-import
from dockci.server import CONFIG
from dockci.util import (bytes_human_readable,
                         docker_ensure_image,
                         FauxDockerLog,
                         is_docker_id,
                         is_semantic,
                         stream_write_status,
                         )


TAG_RE = re.compile('[a-z0-9_.]')


class Build(Model):  # pylint:disable=too-many-instance-attributes
    """
    An individual job build, and result
    """
    def __init__(self, job=None, slug=None):
        super(Build, self).__init__()

        assert job is not None, "Job is given"

        self.job = job
        self.job_slug = job.slug

        if slug:
            self.slug = slug

    slug = OnAccess(lambda _: hex(int(datetime.now().timestamp() * 10000))[2:])
    job = OnAccess(lambda self: Job(self.job_slug))
    job_slug = OnAccess(lambda self: self.job.slug)  # TODO infinite loop
    ancestor_build = ModelReference(lambda self: Build(
        self.job,
        self.ancestor_build_slug
    ), default=lambda _: None)
    create_ts = LoadOnAccess(generate=lambda _: datetime.now())
    start_ts = LoadOnAccess(default=lambda _: None)
    complete_ts = LoadOnAccess(default=lambda _: None)
    result = LoadOnAccess(default=lambda _: None)
    repo = LoadOnAccess(generate=lambda self: self.job.repo)
    commit = LoadOnAccess(default=lambda _: None)
    tag = LoadOnAccess(default=lambda _: None)
    image_id = LoadOnAccess(default=lambda _: None)
    container_id = LoadOnAccess(default=lambda _: None)
    exit_code = LoadOnAccess(default=lambda _: None)
    docker_client_host = LoadOnAccess(
        generate=lambda self: self.docker_client.base_url,
    )
    build_stage_slugs = LoadOnAccess(generate=lambda _: [])
    build_stages = OnAccess(lambda self: [
        BuildStage(build=self, slug=slug)
        for slug
        in self.build_stage_slugs
    ])
    git_author_name = LoadOnAccess(default=lambda _: None)
    git_author_email = LoadOnAccess(default=lambda _: None)
    git_committer_name = LoadOnAccess(default=lambda self:
                                      self.git_author_name)
    git_committer_email = LoadOnAccess(default=lambda self:
                                       self.git_author_email)
    git_changes = LoadOnAccess(default=lambda _: None)
    # pylint:disable=unnecessary-lambda
    build_config = OnAccess(lambda self: BuildConfig(self))

    _provisioned_containers = []

    def validate(self):
        with self.parent_validation(Build):
            errors = []

            if not self.job:
                errors.append("Parent job not given")
            if self.image_id and not is_docker_id(self.image_id):
                errors.append("Invalid Docker image ID")
            if self.container_id and not is_docker_id(self.container_id):
                errors.append("Invalid Docker container ID")

            if errors:
                raise ValidationError(errors)

        return True

    @property
    def state(self):
        """
        Current state that the build is in
        """
        if self.result is not None:
            return self.result
        elif self.build_stages:
            return 'running'  # TODO check if running or dead
        else:
            return 'queued'  # TODO check if queued or queue fail

    _docker_client = None

    @property
    def docker_client(self):
        """
        Get the cached (or new) Docker Client object being used for this build

        CACHED VALUES NOT AVAILABLE OUTSIDE FORK
        """
        if not self._docker_client:
            if self.has_value('docker_client_host'):
                docker_client_args = {'base_url': self.docker_client_host}

            elif CONFIG.docker_use_env_vars:
                docker_client_args = kwargs_from_env()

            else:
                docker_client_args = {
                    # TODO real load balancing, queueing
                    'base_url': random.choice(CONFIG.docker_hosts),
                }

            self._docker_client = docker.Client(**docker_client_args)
            self.save()

        return self._docker_client

    @property
    def build_output_details(self):
        """
        Details for build output artifacts
        """
        # pylint:disable=no-member
        output_files = (
            (name, self.build_output_path().join('%s.tar' % name))
            for name in self.build_config.build_output.keys()
        )
        return {
            name: {'size': bytes_human_readable(path.size()),
                   'link': url_for('build_output_view',
                                   job_slug=self.job_slug,
                                   build_slug=self.slug,
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
                                          name=self.job_slug)

        return self.job_slug

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
        Check if this is a successfully run, tagged build
        """
        return self.result == 'success' and self.tag is not None

    def data_file_path(self):
        # Add the job name before the build slug in the path
        data_file_path = super(Build, self).data_file_path()
        return data_file_path.join(
            '..', self.job.slug, data_file_path.basename
        )

    def build_output_path(self):
        """
        Directory for any build output data
        """
        return self.data_file_path().join('..', '%s_output' % self.slug)

    def queue(self):
        """
        Add the build to the queue
        """
        if self.start_ts:
            raise AlreadyRunError(self)

        # TODO fix and reenable pylint check for cyclic-import
        from dockci.workers import run_build_async
        run_build_async(self.job_slug, self.slug)

    def _run_now(self):
        """
        Worker func that performs the build
        """
        self.start_ts = datetime.now()
        self.save()

        try:
            with tempfile.TemporaryDirectory() as workdir:
                workdir = py.path.local(workdir)
                pre_build = (stage() for stage in (
                    lambda: self._stage(
                        runnable=WorkdirStage(self, workdir)
                    ).returncode == 0,
                    lambda: self._stage(
                        runnable=GitInfoStage(self, workdir)
                    ).returncode == 0,
                    lambda: self._run_git_changes(workdir),
                    lambda: self._run_tag_version(workdir),
                    lambda: self._run_provision(workdir),
                    lambda: self._run_build(workdir),
                ))
                if not all(pre_build):
                    self.result = 'error'
                    return False

                if not self._run_test():
                    self.result = 'fail'
                    return False

                # We should fail the build here because if this is a tagged
                # build, we can't rebuild it
                if not self._run_push():
                    self.result = 'error'
                    return False

                self.result = 'success'
                self.save()

                # Failing this doesn't indicade build failure
                # TODO what kind of a failure would this not working be?
                self._run_fetch_output()

            return True
        except Exception:  # pylint:disable=broad-except
            self.result = 'error'
            self._error_stage('error')

            return False

        finally:
            try:
                self._run_cleanup()

            except Exception:  # pylint:disable=broad-except
                self._error_stage('cleanup_error')

            self.complete_ts = datetime.now()
            self.save()

    def _run_git_changes(self, workdir):
        """
        Get a list of changes from git between now and the most recently built
        ancestor
        """
        # TODO fix YAML model to return None rather than an empty model so that
        #      if self.ancestor_build will work
        if self.has_value('ancestor_build'):
            revision_range_string = '%s..%s' % (
                self.ancestor_build.commit,  # pylint:disable=no-member
                self.commit,
            )
            self._stage(
                'git_changes',
                workdir=workdir,
                cmd_args=['git',
                          '-c', 'color.ui=always',
                          'log', revision_range_string
                          ]
            )

        return True  # Result is irrelevant

    def _run_tag_version(self, workdir):
        """
        Try and add a version to the build, based on git tag
        """
        stage = self._stage(
            'git_tag', workdir=workdir,
            cmd_args=['git', 'describe', '--tags', '--exact-match']
        )
        if not stage.returncode == 0:
            # TODO remove spoofed return
            # (except that --exact-match legitimately returns 128 if no tag)
            return True  # stage result is irrelevant

        try:
            # TODO opening file to get this is kinda awful
            with stage.data_file_path().open() as handle:
                last_line = None
                for line in handle:
                    line = line.strip()
                    if line and TAG_RE.match(line):
                        last_line = line

                if last_line:
                    self.tag = last_line
                    self.save()

        except KeyError:
            pass

        # TODO don't spoof the return; just ignore output elsewhere
        return True  # stage result is irrelevant

    def _run_provision(self, workdir):
        """
        Provision the services that are required for this build
        """
        def runnable(handle):
            """
            Resolve jobs and start services
            """
            all_okay = True
            # pylint:disable=no-member
            for job_slug, service_config in self.build_config.services.items():
                faux_log = FauxDockerLog(handle)

                defaults = {'status': "Finding service %s" % job_slug,
                            'id': 'docker_provision_%s' % job_slug}
                with faux_log.more_defaults(**defaults):
                    faux_log.update()

                    service_job = Job(job_slug)
                    if not service_job.exists():
                        faux_log.update(error="No job found")
                        all_okay = False
                        continue

                    service_build = service_job.latest_build(passed=True,
                                                             versioned=True)
                    if not service_build:
                        faux_log.update(
                            error="No successful, versioned build for %s" % (
                                service_job.name
                            ),
                        )
                        all_okay = False
                        continue

                defaults = {
                    'status': "Starting service %s %s" % (
                        service_job.name,
                        service_build.tag,
                    ),
                    'id': 'docker_provision_%s' % job_slug}
                with faux_log.more_defaults(**defaults):
                    faux_log.update()

                    try:
                        image_id = docker_ensure_image(
                            self.docker_client,
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
                        container = self.docker_client.create_container(
                            image=image_id,
                            **service_kwargs
                        )
                        self.docker_client.start(container['Id'])

                        # Store the provisioning info
                        self._provisioned_containers.append({
                            'job_slug': job_slug,
                            'config': service_config,
                            'id': container['Id']
                        })
                        faux_log.update(progress="Done")

                    except docker.errors.APIError as ex:
                        faux_log.update(error=ex.explanation.decode())
                        all_okay = False

            return all_okay

        return self._stage('docker_provision',
                           workdir=workdir,
                           runnable=runnable).returncode

    def _run_build(self, workdir):
        """
        Tell the Docker host to build
        """
        def on_done(line):
            """
            Check the final line for success, and image id
            """
            if line:
                if isinstance(line, bytes):
                    line = line.decode()

                line_data = json.loads(line)
                re_match = re.search(r'Successfully built ([0-9a-f]+)',
                                     line_data.get('stream', ''))
                if re_match:
                    self.image_id = re_match.group(1)
                    return True

            return False

        tag = self.docker_full_name
        if self.tag is not None:
            existing_image = None
            for image in self.docker_client.images(
                name=self.job_slug,
            ):
                if tag in image['RepoTags']:
                    existing_image = image
                    break

            if existing_image is not None:
                # Do not override existing builds of _versioned_ tagged code
                if is_semantic(self.tag):
                    raise AlreadyBuiltError(
                        'Version %s of %s already built' % (
                            self.tag,
                            self.job_slug,
                        )
                    )
                # Delete existing builds of _non-versioned_ tagged code
                # (allows replacement of images)
                else:
                    # TODO it would be nice to inform the user of this action
                    try:
                        self.docker_client.remove_image(
                            image=existing_image['Id'],
                        )
                    except docker.errors.APIError:
                        # TODO handle deletion of containers here
                        pass

        # Don't use the docker caches if a version tag is defined
        no_cache = (self.tag is not None)

        return self._run_docker(
            'build',
            # saved stream for debugging
            # lambda: open('docker_build_stream', 'r'),
            lambda: self.docker_client.build(path=workdir.strpath,
                                             tag=tag,
                                             nocache=no_cache,
                                             rm=True,
                                             stream=True),
            on_done=on_done,
        )

    def _run_test(self):
        """
        Tell the Docker host to run the CI command
        """
        def start_container():
            """
            Create a container instance, attache to its outputs and then start
            it, returning the output stream
            """
            container_details = self.docker_client.create_container(
                self.image_id, 'ci'
            )
            self.container_id = container_details['Id']
            self.save()

            def link_tuple(service_info):
                """
                Turn our provisioned service info dict into an alias string for
                Docker
                """
                if 'name' not in service_info:
                    service_info['name'] = \
                        self.docker_client.inspect_container(
                            service_info['id']
                        )['Name'][1:]  # slice to remove the / from start

                if 'alias' not in service_info:
                    if isinstance(service_info['config'], dict):
                        service_info['alias'] = service_info['config'].get(
                            'alias',
                            service_info['job_slug']
                        )

                    else:
                        service_info['alias'] = service_info['job_slug']

                return (service_info['name'], service_info['alias'])

            stream = self.docker_client.attach(self.container_id, stream=True)
            self.docker_client.start(
                self.container_id,
                links=[
                    link_tuple(service_info)
                    for service_info in self._provisioned_containers
                ]
            )

            return stream

        def on_done(_):
            """
            Check container exit code and return True on 0, or False otherwise
            """
            details = self.docker_client.inspect_container(self.container_id)
            self.exit_code = details['State']['ExitCode']
            self.save()
            return self.exit_code == 0

        return self._run_docker(
            'test',
            start_container,
            on_done=on_done,
        )

    def _run_push(self):
        """
        Push the built container to the Docker registry, if versioned and
        configured
        """
        def push_container():
            """
            Perform the actual Docker push operation
            """
            return self.docker_client.push(
                self.docker_image_name,
                tag=self.tag,
                stream=True,
                insecure_registry=CONFIG.docker_registry_insecure,
            )

        if self.tag and CONFIG.docker_use_registry:
            return self._run_docker('push', push_container)

        else:
            return True

    def _run_docker(self,
                    docker_stage_slug,
                    docker_command,
                    on_line=None,
                    on_done=None):
        """
        Wrapper around common Docker command process. Will send output lines to
        file, and optionally use callbacks to notify on each line, and
        completion
        """
        def runnable(handle):
            """
            Perform the Docker command given
            """
            output = docker_command()

            line = ''
            for line in output:
                if isinstance(line, bytes):
                    handle.write(line)
                else:
                    handle.write(line.encode())

                handle.flush()

                if on_line:
                    on_line(line)

            if on_done:
                return on_done(line)

            elif line:
                return True

            return False

        return self._stage('docker_%s' % docker_stage_slug,
                           runnable=runnable).returncode

    def _run_fetch_output(self):
        """
        Fetches any output specified in build config
        """

        def runnable(handle):
            """
            Fetch/save the files
            """
            # pylint:disable=no-member
            mappings = self.build_config.build_output.items()
            for key, docker_fn in mappings:
                handle.write(
                    ("Fetching %s from '%s'..." % (key, docker_fn)).encode()
                )
                resp = self.docker_client.copy(self.container_id, docker_fn)

                if 200 <= resp.status < 300:
                    output_path = self.build_output_path().join('%s.tar' % key)
                    with output_path.open('wb') as output_fh:
                        # TODO stream so that not buffered in RAM
                        bytes_written = output_fh.write(resp.data)

                    handle.write(
                        (" DONE! %s total\n" % (
                            bytes_human_readable(bytes_written)
                        )).encode(),
                    )

                else:
                    handle.write(
                        (" FAIL! HTTP status %d: %s\n" % (
                            resp.status_code, resp.reason
                        )).encode(),
                    )

            # Output something on no output
            if not mappings:
                handle.write("No output files to fetch".encode())

        return self._stage('docker_fetch', runnable=runnable).returncode

    def _run_cleanup(self):
        """
        Clean up after the build/test
        """
        def cleanup_context(handle, object_type, object_id):
            """
            Get a stream_write_status context manager with messages set
            correctly
            """
            return stream_write_status(
                handle,
                "Cleaning up %s '%s'..." % (object_type, object_id),
                "DONE!",
                "FAILED!",
            )

        def runnable(handle):
            """
            Do the image/container cleanup
            """
            if self.container_id:
                with cleanup_context(handle, 'container', self.container_id):
                    self.docker_client.remove_container(self.container_id)

            if self._provisioned_containers:
                for service_info in self._provisioned_containers:
                    ctx = cleanup_context(handle,
                                          'provisioned container',
                                          service_info['id'])
                    with ctx:
                        self.docker_client.remove_container(
                            service_info['id'],
                            force=True,
                        )

            # Only clean up image if this is an non-tagged build
            if self.tag is None or self.result in ('error', 'fail'):
                if self.image_id:
                    with cleanup_context(handle, 'image', self.image_id):
                        self.docker_client.remove_image(self.image_id)

        return self._stage('cleanup', runnable)

    def _error_stage(self, stage_slug):
        """
        Create an error stage and add stack trace for it
        """
        self.build_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()

        import traceback
        try:
            BuildStage(
                self,
                stage_slug,
                lambda handle: handle.write(
                    bytes(traceback.format_exc(), 'utf8')
                )
            ).run()
        except Exception:  # pylint:disable=broad-except
            print(traceback.format_exc())

    def _stage(self,
               stage_slug=None,
               runnable=None,
               workdir=None,
               cmd_args=None):
        """
        Create and save a new build stage, running the given args and saving
        its output
        """
        if cmd_args:
            stage = BuildStage.from_command(build=self,
                                            slug=stage_slug,
                                            cwd=workdir,
                                            cmd_args=cmd_args)
        elif isinstance(runnable, BuildStageBase):
            stage = runnable
            stage_slug = runnable.slug
        else:
            stage = BuildStage(build=self, slug=stage_slug, runnable=runnable)

        logging.getLogger('dockci.build.stages').debug(
            "Starting '%s' build stage for build '%s'", stage_slug, self.slug
        )

        self.build_stage_slugs.append(stage_slug)  # pylint:disable=no-member
        self.save()

        stage.run()
        return stage
