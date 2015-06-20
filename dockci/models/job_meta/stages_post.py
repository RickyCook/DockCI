"""
Job stages that occur after a job is complete
"""

from dockci.models.job_meta.stages import JobStageBase, DockerStage
from dockci.server import CONFIG
from dockci.util import bytes_human_readable, stream_write_status


class PushStage(DockerStage):
    """
    Push the built container to the Docker registry, if versioned and
    configured
    """

    slug = 'docker_push'

    def runnable_docker(self):
        """
        Perform the actual Docker push operation
        """
        if self.job.tag and CONFIG.docker_use_registry:
            return self.job.docker_client.push(
                self.job.docker_image_name,
                tag=self.job.tag,
                stream=True,
                insecure_registry=CONFIG.docker_registry_insecure,
            )


class FetchStage(JobStageBase):
    """
    Fetches any output specified in job config
    """

    slug = 'docker_fetch'

    def runnable(self, handle):
        """
        Fetch/save the files
        """
        # pylint:disable=no-member
        mappings = self.job.job_config.job_output.items()
        for key, docker_fn in mappings:
            handle.write(
                ("Fetching %s from '%s'..." % (key, docker_fn)).encode()
            )
            resp = self.job.docker_client.copy(
                self.job.container_id, docker_fn,
            )

            if 200 <= resp.status < 300:
                output_path = self.job.job_output_path().join(
                    '%s.tar' % key
                )
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
                return 1

        # Output something on no output
        if not mappings:
            handle.write("No output files to fetch".encode())

        return 0


class CleanupStage(JobStageBase):
    """
    Clean up after the job/test
    """

    slug = 'cleanup'

    def runnable(self, handle):
        """
        Do the image/container cleanup
        """
        def cleanup_context(object_type, object_id):
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

        if self.job.container_id:
            with cleanup_context('container', self.job.container_id):
                self.job.docker_client.remove_container(
                    self.job.container_id,
                )

        # pylint:disable=protected-access
        if self.job._provisioned_containers:
            for service_info in self.job._provisioned_containers:
                ctx = cleanup_context('provisioned container',
                                      service_info['id'])
                with ctx:
                    self.job.docker_client.remove_container(
                        service_info['id'],
                        force=True,
                    )

        # Only clean up image if this is an non-tagged job
        if self.job.tag is None or self.job.result in ('broken', 'fail'):
            if self.job.image_id:
                with cleanup_context('image', self.job.image_id):
                    self.job.docker_client.remove_image(self.job.image_id)

        # TODO catch failures
        return 0
