"""
Functions and constants relating to background workers
"""
import logging
import os

from flask_mail import Message

from dockci.models.build import Build
from dockci.models.job import Job
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


def run_build_async(job_slug, build_slug):
    """
    Load and run a build's private run job, forking to handle the build in the
    background
    """
    if os.fork():
        return  # parent process

    logger = logging.getLogger('dockci.build')
    try:
        with APP.app_context():
            job = Job(job_slug)
            build = Build(job=job, slug=build_slug)
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
                    email = Message(
                        recipients=recipients,
                        subject="DockCI - {job_name} {build_result}ed".format(
                            job_name=job.name,
                            build_result=build.result,
                        ),
                    )
                    send_mail(email)

            # Send a HipChat notification
            if job.hipchat_api_token != '' and job.hipchat_room != '':
                hipchat = HipChat(apitoken=job.hipchat_api_token,
                                  room=job.hipchat_room)
                hipchat.message("DockCI - {name} Build {id}: {result}".format(
                    name=job.name,
                    id=build.create_ts,
                    result=build.result,
                ))

    except Exception:  # pylint:disable=broad-except
        logger.exception("Something went wrong in the build worker")
