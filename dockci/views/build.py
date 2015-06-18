"""
Views related to build management
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

from dockci.models.build import Build
from dockci.models.project import Project
from dockci.server import APP
from dockci.util import (login_or_github_required,
                         is_valid_github,
                         DateTimeEncoder,
                         )


@APP.route('/projects/<project_slug>/builds/<build_slug>', methods=('GET',))
def build_view(project_slug, build_slug):
    """
    View to display a build
    """
    project = Project(slug=project_slug)
    build = Build(project=project, slug=build_slug)
    if not build.exists():
        abort(404)

    return render_template('build.html', build=build)


@APP.route('/projects/<project_slug>/builds/new', methods=('GET', 'POST'))
@login_or_github_required
def build_new_view(project_slug):
    """
    View to create a new build
    """
    project = Project(slug=project_slug)
    if not project.exists():
        abort(404)

    if request.method == 'POST':
        build = Build(project=project)
        build.repo = project.repo

        build_url = url_for('build_view',
                            project_slug=project_slug,
                            build_slug=build.slug)

        if 'X-Github-Event' in request.headers:
            if not project.github_secret:
                logging.warn("GitHub webhook secret not setup")
                abort(403)

            if not is_valid_github(project.github_secret):
                logging.warn("Invalid GitHub payload")
                abort(403)

            if request.headers['X-Github-Event'] == 'push':
                push_data = request.json
                build.commit = push_data['head_commit']['id']

            else:
                logging.debug("Unknown GitHub hook '%s'",
                              request.headers['X-Github-Event'])
                abort(501)

            try:
                build.save()
                build.queue()

                return build_url, 201

            except ValidationError as ex:
                logging.exception("GitHub hook error")
                return json.dumps({
                    'errors': ex.messages,
                }), 400

        else:
            build.commit = request.form['commit']

            try:
                build.save()
                build.queue()

                flash(u"Build queued", 'success')
                return redirect(build_url, 303)

            except ValidationError as ex:
                flash(ex.messages, 'danger')

    return render_template('build_new.html', build=Build(project=project))


@APP.route('/projects/<project_slug>/builds/<build_slug>.json',
           methods=('GET',))
def build_output_json(project_slug, build_slug):
    """
    View to download some build info in JSON
    """
    project = Project(slug=project_slug)
    build = Build(project=project, slug=build_slug)
    if not build.exists():
        abort(404)

    return Response(json.dumps(build.as_dict(),
                               cls=DateTimeEncoder
                               ),
                    mimetype='application/json')


@APP.route('/projects/<project_slug>/builds/<build_slug>/output/<filename>',
           methods=('GET',))
def build_output_view(project_slug, build_slug, filename):
    """
    View to download some build output
    """
    project = Project(slug=project_slug)
    build = Build(project=project, slug=build_slug)

    # TODO possible security issue opending files from user input like this
    data_file_path = build.build_output_path().join(filename)
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
                    build.state == 'running' and
                    filename == "%s.log" % build.build_stage_slugs[-1]
                )
                if is_live_log:
                    select.select((handle,), (), (), 2)
                    build.load()

                elif len(data) == 0:
                    return

    mimetype, _ = mimetypes.guess_type(filename)
    if mimetype is None:
        mimetype = 'application/octet-stream'

    return Response(loader(), mimetype=mimetype)
