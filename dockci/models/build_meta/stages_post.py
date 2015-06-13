"""
Build stages that occur after a build is complete
"""

from dockci.models.build_meta.stages import BuildStageBase, DockerStage
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
        if self.build.tag and CONFIG.docker_use_registry:
            return self.build.docker_client.push(
                self.build.docker_image_name,
                tag=self.build.tag,
                stream=True,
                insecure_registry=CONFIG.docker_registry_insecure,
            )


class FetchStage(BuildStageBase):
    """
    Fetches any output specified in build config
    """

    slug = 'docker_fetch'

    def runnable(self, handle):
        """
        Fetch/save the files
        """
        # pylint:disable=no-member
        mappings = self.build.build_config.build_output.items()
        for key, docker_fn in mappings:
            handle.write(
                ("Fetching %s from '%s'..." % (key, docker_fn)).encode()
            )
            resp = self.build.docker_client.copy(
                self.build.container_id, docker_fn,
            )

            if 200 <= resp.status < 300:
                output_path = self.build.build_output_path().join(
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


class CleanupStage(BuildStageBase):
    """
    Clean up after the build/test
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

        if self.build.container_id:
            with cleanup_context('container', self.build.container_id):
                self.build.docker_client.remove_container(
                    self.build.container_id,
                )

        # pylint:disable=protected-access
        if self.build._provisioned_containers:
            for service_info in self.build._provisioned_containers:
                ctx = cleanup_context('provisioned container',
                                      service_info['id'])
                with ctx:
                    self.build.docker_client.remove_container(
                        service_info['id'],
                        force=True,
                    )

        # Only clean up image if this is an non-tagged build
        if self.build.tag is None or self.build.result in ('error', 'fail'):
            if self.build.image_id:
                with cleanup_context('image', self.build.image_id):
                    self.build.docker_client.remove_image(self.build.image_id)

        # TODO catch failures
        return 0
