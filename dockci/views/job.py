"""
Views related to job management
"""

import re

from flask import abort, flash, redirect, render_template, request
from flask_security import current_user, login_required

from dockci.models.job import Job
from dockci.server import APP
from dockci.util import model_flash, request_fill


@APP.route('/jobs/<slug>', methods=('GET', 'POST'))
def job_view(slug):
    """
    View to display a job
    """
    job = Job(slug)
    if not job.exists():
        abort(404)

    request_fill(job, ('name', 'repo', 'github_secret',
                       'hipchat_api_token', 'hipchat_room'))

    page_size = int(request.args.get('page_size', 20))
    page_offset = int(request.args.get('page_offset', 0))
    versioned = 'versioned' in request.args

    if versioned:
        builds = list(job.filtered_builds(passed=True, versioned=True))
    else:
        builds = job.builds

    prev_page_offset = max(page_offset - page_size, 0)
    if page_offset < 1:
        prev_page_offset = None

    next_page_offset = page_offset + page_size
    if next_page_offset > len(builds):
        next_page_offset = None

    builds = builds[page_offset:page_offset + page_size]
    return render_template('job.html',
                           job=job,
                           builds=builds,
                           versioned=versioned,
                           prev_page_offset=prev_page_offset,
                           next_page_offset=next_page_offset,
                           page_size=page_size)


@APP.route('/jobs/<slug>/edit', methods=('GET', 'POST'))
@login_required
def job_edit_view(slug):
    """
    View to edit a job
    """
    job = Job(slug)
    if not job.exists():
        abort(404)

    return job_input_view(job, 'edit', [
        'name', 'repo', 'github_secret'
        'hipchat_api_token', 'hipchat_room',
    ])


@APP.route('/jobs/new', methods=('GET', 'POST'))
@login_required
def job_new_view():
    """
    View to make a new job
    """
    job = Job()
    return job_input_view(job, 'new', [
        'slug', 'name', 'repo', 'github_secret', 'github_repo_id',
        'hipchat_api_token', 'hipchat_room',
    ])


def handle_github_hook(job):
    """ Try to add a GitHub hook for a job """
    if model_flash(job, save=False):
        result = job.add_github_webhook()  # auto saves on success

        if result.status == 201:
            return True

        else:
            flash(result.data.get(
                'message',
                ("Unexpected response from GitHub. "
                 "HTTP status %d") % result.status
            ), 'danger')

    return False


def job_input_view(job, edit_operation, fields):
    """ Generic view for job editing """
    if request.method == 'POST':
        fill_data = request.form.to_dict()

        # Filter out github properties if not a github repo, so that they are
        # unset on the job
        if request.args.get('repo_type', None) == 'github':
            fill_data['github_auth_user'] = current_user
            fields.append('github_auth_user')
        else:
            fill_data['github_repo_id'] = None
            fields.append('github_repo_id')

        saved = request_fill(
            job, fields,
            data=fill_data,
            save=request.args.get('repo_type', None) != 'github',
        )

        if request.args.get('repo_type', None) == 'github':
            saved = handle_github_hook(job)

        if saved:
            return redirect('/jobs/{job_slug}'.format(job_slug=job.slug))

    if 'repo_type' in request.args:
        default_repo_type = request.args['repo_type']

    elif current_user is None or not current_user.is_authenticated():
        default_repo_type = 'manual'

    elif 'github' in current_user.oauth_tokens:
        default_repo_type = 'github'

    else:
        default_repo_type = 'manual'

    re.sub(r'[^\w\s]', '', default_repo_type)

    return render_template('job_edit.html',
                           job=job,
                           edit_operation=edit_operation,
                           default_repo_type=default_repo_type,
                           )
