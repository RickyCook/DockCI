"""
Main build stages that constitute a build
"""

import json
import re

import docker

from dockci.exceptions import AlreadyBuiltError
from dockci.models.build_meta.stages import DockerStage
from dockci.util import is_semantic


class BuildDockerStage(DockerStage):
    """
    Tell the Docker host to build
    """

    slug = 'docker_build'
    built_re = re.compile(r'Successfully built ([0-9a-f]+)')

    def __init__(self, build, workdir):
        super(BuildDockerStage, self).__init__(build)
        self.workdir = workdir
        self.tag = None
        self.no_cache = None

    def runnable_docker(self):
        """
        Determine the image tag, and cache flag value, then trigger a Docker
        image build, returning the output stream so that DockerStage can handle
        the output
        """
        tag = self.build.docker_full_name
        if self.build.tag is not None:
            existing_image = None
            for image in self.build.docker_client.images(
                name=self.build.job_slug,
            ):
                if tag in image['RepoTags']:
                    existing_image = image
                    break

            if existing_image is not None:
                # Do not override existing builds of _versioned_ tagged code
                if is_semantic(self.build.tag):
                    raise AlreadyBuiltError(
                        'Version %s of %s already built' % (
                            self.build.tag,
                            self.build.job_slug,
                        )
                    )
                # Delete existing builds of _non-versioned_ tagged code
                # (allows replacement of images)
                else:
                    # TODO it would be nice to inform the user of this action
                    try:
                        self.build.docker_client.remove_image(
                            image=existing_image['Id'],
                        )
                    except docker.errors.APIError:
                        # TODO handle deletion of containers here
                        pass

        # Don't use the docker caches if a version tag is defined
        no_cache = (self.build.tag is not None)

        return self.build.docker_client.build(path=self.workdir.strpath,
                                              tag=tag,
                                              nocache=no_cache,
                                              rm=True,
                                              stream=True)

    def on_done(self, line):
        """
        Check the final line for success, and image id
        """
        if line:
            if isinstance(line, bytes):
                line = line.decode()

            line_data = json.loads(line)
            re_match = self.built_re.search(line_data.get('stream', ''))
            if re_match:
                self.build.image_id = re_match.group(1)
                return 0

        return 1
