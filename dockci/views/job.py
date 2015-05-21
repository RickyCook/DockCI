"""
Views related to job management
"""

from flask import abort, redirect, render_template, request
from flask.ext.security import login_required

from dockci.models.job import Job
from dockci.server import APP
from dockci.util import request_fill


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


@APP.route('/jobs/<slug>/edit', methods=('GET',))
@login_required
def job_edit_view(slug):
    """
    View to edit a job
    """
    job = Job(slug)
    if not job.exists():
        abort(404)

    return render_template('job_edit.html',
                           job=job,
                           edit_operation='edit')


@APP.route('/jobs/new', methods=('GET', 'POST'))
@login_required
def job_new_view():
    """
    View to make a new job
    """
    job = Job()
    if request.method == 'POST':
        saved = request_fill(job, ('slug', 'name', 'repo',
                                   'hipchat_api_token', 'hipchat_room'))
        if saved:
            return redirect('/jobs/{job_slug}'.format(job_slug=job.slug))

    return render_template('job_edit.html', job=job, edit_operation='new')
