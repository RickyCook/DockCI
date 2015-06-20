"""
Functions and constants relating to background workers
"""
import logging
import os

from flask_mail import Message

from dockci.models.build import Build
from dockci.models.project import Project
from dockci.server import APP, MAIL
from dockci.notifications import HipChat


def send_mail(message):
    """
    Send an email using the app context
    """
    with APP.app_context():
        try:
            MAIL.send(message)

        except Exception:  # pylint:disable=broad-except
            logging.getLogger('dockci.mail').exception(
                "Couldn't send email message"
            )


def run_build_async(project_slug, build_slug):
    """
    Load and run a build's private run project, forking to handle the build in
    the background
    """
    if os.fork():
        return  # parent process

    logger = logging.getLogger('dockci.build')
    try:
        with APP.app_context():
            project = Project(project_slug)
            build = Build(project=project, slug=build_slug)
            build_okay = build._run_now()  # pylint:disable=protected-access

            # Send the failure message
            if not build_okay:
                recipients = []
                if build.git_author_email:
                    recipients.append('%s <%s>' % (
                        build.git_author_name,
                        build.git_author_email
                    ))
                if build.git_committer_email:
                    recipients.append('%s <%s>' % (
                        build.git_committer_name,
                        build.git_committer_email
                    ))

                if recipients:
                    subject = (
                        "DockCI - {project_name} {build_result}ed".format(
                            project_name=project.name,
                            build_result=build.result,
                        )
                    )
                    email = Message(
                        recipients=recipients,
                        subject=subject,
                    )
                    send_mail(email)

            # Send a HipChat notification
            if project.hipchat_api_token != '' and project.hipchat_room != '':
                hipchat = HipChat(apitoken=project.hipchat_api_token,
                                  room=project.hipchat_room)
                hipchat.message("DockCI - {name} Build {id}: {result}".format(
                    name=project.name,
                    id=build.create_ts,
                    result=build.result,
                ))

    except Exception:  # pylint:disable=broad-except
        logger.exception("Something went wrong in the build worker")
