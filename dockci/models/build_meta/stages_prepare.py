"""
Preparation for the main build stages
"""

from dockci.models.build_meta.config import BuildConfig
from dockci.models.build_meta.stages import CommandBuildStage


class WorkdirStage(CommandBuildStage):
    """ Prepare the working directory """

    def __init__(self, build, workdir):
        super(WorkdirStage, self).__init__(
            'git_prepare', build, workdir, (
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
