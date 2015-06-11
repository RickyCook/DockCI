"""
Build stages that occur after a build is complete
"""

from dockci.models.build_meta.stages import DockerStage
from dockci.server import CONFIG


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
