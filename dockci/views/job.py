"""
Views related to job management
"""

import json
import logging
import mimetypes
import select

from flask import (abort,
                   flash,
                   redirect,
                   render_template,
                   request,
                   Response,
                   url_for,
                   )
from yaml_model import ValidationError

from dockci.models.job import Job
from dockci.models.project import Project
from dockci.server import APP
from dockci.util import (login_or_github_required,
                         is_valid_github,
                         DateTimeEncoder,
                         )


@APP.route('/projects/<project_slug>/jobs/<job_slug>', methods=('GET',))
def job_view(project_slug, job_slug):
    """
    View to display a job
    """
    project = Project(slug=project_slug)
    job = Job(project=project, slug=job_slug)
    if not job.exists():
        abort(404)

    return render_template('job.html', job=job)


@APP.route('/projects/<project_slug>/jobs/new', methods=('GET', 'POST'))
@login_or_github_required
def job_new_view(project_slug):
    """
    View to create a new job
    """
    project = Project(slug=project_slug)
    if not project.exists():
        abort(404)

    if request.method == 'POST':
        job = Job(project=project)
        job.repo = project.repo

        job_url = url_for('job_view',
                          project_slug=project_slug,
                          job_slug=job.slug)

        if 'X-Github-Event' in request.headers:
            if not project.github_secret:
                logging.warn("GitHub webhook secret not setup")
                abort(403)

            if not is_valid_github(project.github_secret):
                logging.warn("Invalid GitHub payload")
                abort(403)

            if request.headers['X-Github-Event'] == 'push':
                push_data = request.json
                job.commit = push_data['head_commit']['id']

            else:
                logging.debug("Unknown GitHub hook '%s'",
                              request.headers['X-Github-Event'])
                abort(501)

            try:
                job.save()
                job.queue()

                return job_url, 201

            except ValidationError as ex:
                logging.exception("GitHub hook error")
                return json.dumps({
                    'errors': ex.messages,
                }), 400

        else:
            job.commit = request.form['commit']

            try:
                job.save()
                job.queue()

                flash(u"Job queued", 'success')
                return redirect(job_url, 303)

            except ValidationError as ex:
                flash(ex.messages, 'danger')

    return render_template('job_new.html', job=Job(project=project))


@APP.route('/projects/<project_slug>/jobs/<job_slug>.json',
           methods=('GET',))
def job_output_json(project_slug, job_slug):
    """
    View to download some job info in JSON
    """
    project = Project(slug=project_slug)
    job = Job(project=project, slug=job_slug)
    if not job.exists():
        abort(404)

    return Response(json.dumps(job.as_dict(),
                               cls=DateTimeEncoder
                               ),
                    mimetype='application/json')


@APP.route('/projects/<project_slug>/jobs/<job_slug>/output/<filename>',
           methods=('GET',))
def job_output_view(project_slug, job_slug, filename):
    """
    View to download some job output
    """
    project = Project(slug=project_slug)
    job = Job(project=project, slug=job_slug)

    # TODO possible security issue opending files from user input like this
    data_file_path = job.job_output_path().join(filename)
    if not data_file_path.check(file=True):
        abort(404)

    def loader():
        """
        Generator to stream the log file
        """
        with data_file_path.open('rb') as handle:
            while True:
                data = handle.read(1024)
                yield data

                is_live_log = (
                    job.state == 'running' and
                    filename == "%s.log" % job.job_stage_slugs[-1]
                )
                if is_live_log:
                    select.select((handle,), (), (), 2)
                    job.load()

                elif len(data) == 0:
                    return

    mimetype, _ = mimetypes.guess_type(filename)
    if mimetype is None:
        mimetype = 'application/octet-stream'

    return Response(loader(), mimetype=mimetype)
