"""
Functions and constants relating to background workers
"""
import logging
import os

from flask_mail import Message

from dockci.models.job import Job
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


def run_job_async(project_slug, job_slug):
    """
    Load and run a job's private run project, forking to handle the job in
    the background
    """
    if os.fork():
        return  # parent process

    logger = logging.getLogger('dockci.job')
    try:
        with APP.app_context():
            project = Project(project_slug)
            job = Job(project=project, slug=job_slug)
            job_okay = job._run_now()  # pylint:disable=protected-access

            # Send the failure message
            if not job_okay:
                recipients = []
                if job.git_author_email:
                    recipients.append('%s <%s>' % (
                        job.git_author_name,
                        job.git_author_email
                    ))
                if job.git_committer_email:
                    recipients.append('%s <%s>' % (
                        job.git_committer_name,
                        job.git_committer_email
                    ))

                if recipients:
                    subject = (
                        "DockCI - {project_name} {job_result}ed".format(
                            project_name=project.name,
                            job_result=job.result,
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
                hipchat.message("DockCI - {name} Job {id}: {result}".format(
                    name=project.name,
                    id=job.create_ts,
                    result=job.result,
                ))

    except Exception:  # pylint:disable=broad-except
        logger.exception("Something went wrong in the job worker")
