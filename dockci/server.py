"""
Functions for setting up and starting the DockCI application server
"""

import logging
import mimetypes
import multiprocessing
import os

from contextlib import contextmanager

import flask
import pika
import redis
import rollbar
import rollbar.contrib.flask

from flask import Flask
from flask_dance.consumer import oauth_authorized
from flask_dance.consumer.backend.sqla import SQLAlchemyBackend
from flask_oauthlib.client import OAuth
from flask_security import current_user, Security, SQLAlchemyUserDatastore
from flask_mail import Mail
from flask_migrate import Migrate
from flask_restful import Api
from flask_script import Manager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.pool import NullPool

from dockci.models.config import Config
from dockci.session import SessionSwitchInterface
from dockci.util import project_root, setup_templates

class WrappedSQLAlchemy(SQLAlchemy):
    """ ``SQLAlchemy`` object that makes the ``poolclass`` a ``NullPool`` """
    def apply_pool_defaults(self, app, options):
        options['poolclass'] = NullPool


APP = Flask(__name__)
MAIL = Mail()
CONFIG = Config()
SECURITY = Security()
DB = WrappedSQLAlchemy()
API = Api(APP, prefix='/api/v1')
MANAGER = Manager(APP)
MIGRATE = Migrate(APP, DB, directory='alembic')

APP.config.model = CONFIG  # For templates

APP.session_interface = SessionSwitchInterface(APP)


OAUTH_APPS_SCOPES = {}
OAUTH_APPS_SCOPE_SERIALIZERS = {
    'github': lambda scope: ','.join(sorted(scope.split(','))),
    'gitlab': lambda scope: ','.join(sorted(scope.split(','))),
}

try:
    from flask_debugtoolbar import DebugToolbarExtension
except ImportError:
    TOOLBAR = None
else:
    TOOLBAR = DebugToolbarExtension()


def get_db_uri():
    """ Try to get the DB URI from multiple sources """
    if 'DOCKCI_DB_URI' in os.environ:
        return os.environ['DOCKCI_DB_URI']
    elif (
        'POSTGRES_PORT_5432_TCP_ADDR' in os.environ and
        'POSTGRES_PORT_5432_TCP_PORT' in os.environ and
        'POSTGRES_ENV_POSTGRES_PASSWORD' in os.environ
    ):
        return "postgresql://{user}:{password}@{addr}:{port}/{name}".format(
            addr=os.environ['POSTGRES_PORT_5432_TCP_ADDR'],
            port=os.environ['POSTGRES_PORT_5432_TCP_PORT'],
            password=os.environ['POSTGRES_ENV_POSTGRES_PASSWORD'],
            user=os.environ.get('POSTGRES_ENV_POSTGRES_USER', 'postgres'),
            name=os.environ.get(
                'POSTGRES_ENV_POSTGRES_DB',
                os.environ.get('POSTGRES_ENV_POSTGRES_USER', 'dockci'),
            ),
        )


def app_init():
    """
    Pre-run app setup
    """
    app_init_rollbar()

    logger = logging.getLogger('dockci.init')

    logger.info("Loading app config")

    APP.secret_key = CONFIG.secret

    APP.config['BUNDLE_ERRORS'] = True

    APP.config['MAIL_SERVER'] = CONFIG.mail_server
    APP.config['MAIL_PORT'] = CONFIG.mail_port
    APP.config['MAIL_USE_TLS'] = CONFIG.mail_use_tls
    APP.config['MAIL_USE_SSL'] = CONFIG.mail_use_ssl
    APP.config['MAIL_USERNAME'] = CONFIG.mail_username
    APP.config['MAIL_PASSWORD'] = CONFIG.mail_password
    APP.config['MAIL_DEFAULT_SENDER'] = CONFIG.mail_default_sender

    APP.config['SECURITY_PASSWORD_HASH'] = 'bcrypt'
    APP.config['SECURITY_PASSWORD_SALT'] = CONFIG.security_password_salt
    APP.config['SECURITY_REGISTERABLE'] = CONFIG.security_registerable_form
    APP.config['SECURITY_RECOVERABLE'] = CONFIG.security_recoverable
    APP.config['SECURITY_CHANGEABLE'] = True
    APP.config['SECURITY_EMAIL_SENDER'] = CONFIG.mail_default_sender
    APP.config['REMEMBER_COOKIE_NAME'] = 'dockci_remember_me'
    APP.config['SESSION_COOKIE_NAME'] = 'dockci_session'

    APP.config['RABBITMQ_USER'] = os.environ.get(
        'RABBITMQ_ENV_BACKEND_USER', 'guest')
    APP.config['RABBITMQ_PASSWORD'] = os.environ.get(
        'RABBITMQ_ENV_BACKEND_PASSWORD', 'guest')
    APP.config['RABBITMQ_HOST'] = os.environ.get(
        'RABBITMQ_PORT_5672_TCP_ADDR', 'localhost')
    APP.config['RABBITMQ_PORT'] = int(os.environ.get(
        'RABBITMQ_PORT_5672_TCP_PORT', 5672))

    APP.config['RABBITMQ_USER_FE'] = os.environ.get(
        'RABBITMQ_ENV_FRONTEND_USER', 'guest')
    APP.config['RABBITMQ_PASSWORD_FE'] = os.environ.get(
        'RABBITMQ_ENV_FRONTEND_PASSWORD', 'guest')

    APP.config['REDIS_HOST'] = os.environ.get(
        'REDIS_PORT_6379_ADDR', 'redis')
    APP.config['REDIS_PORT'] = int(os.environ.get(
        'REDIS_PORT_6379_PORT', 6379))

    if APP.config.get('SQLALCHEMY_DATABASE_URI', None) is None:
        APP.config['SQLALCHEMY_DATABASE_URI'] = get_db_uri()

    mimetypes.add_type('application/x-yaml', 'yaml')

    from dockci.models.auth import User, Role
    from dockci.models.job import Job  # pylint:disable=unused-variable
    from dockci.models.project import Project  # pylint:disable=unused-variable

    if 'security' not in APP.blueprints:
        SECURITY.init_app(APP, SQLAlchemyUserDatastore(DB, User, Role))

    MAIL.init_app(APP)
    DB.init_app(APP)

    if TOOLBAR is not None:
        logging.warning('Debug initialized. Enabled: %s', APP.debug)
        TOOLBAR.init_app(APP)

    app_init_oauth()
    app_init_handlers()
    app_init_api()
    app_init_views()
    app_init_workers()


def get_redis_pool():
    """ Create a configured Redis connection pool """
    return redis.ConnectionPool(host=APP.config['REDIS_HOST'],
                                port=APP.config['REDIS_PORT'],
                                )


@contextmanager
def redis_pool():
    """ Context manager for getting and disconnecting a Redis pool """
    pool = get_redis_pool()
    try:
        yield pool

    finally:
        pool.disconnect()


def get_pika_conn():
    """ Create a connection to RabbitMQ """
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=APP.config['RABBITMQ_HOST'],
        port=APP.config['RABBITMQ_PORT'],
        credentials=pika.credentials.PlainCredentials(
            APP.config['RABBITMQ_USER'],
            APP.config['RABBITMQ_PASSWORD'],
        ),
    ))


@contextmanager
def pika_conn():
    """ Context manager for getting and closing a pika connection """
    conn = get_pika_conn()
    try:
        yield conn

    finally:
        conn.close()


def wrapped_report_exception(app, exception):
    """ Wrapper for ``report_exception`` to ignore some exceptions """
    if getattr(exception, 'no_rollbar', False):
        return

    return rollbar.contrib.flask.report_exception(app, exception)


def app_init_rollbar():
    """ Initialize Rollbar for error/exception reporting """
    try:
        api_key = os.environ['ROLLBAR_API_KEY']
        environment = os.environ['ROLLBAR_ENVIRONMENT']
    except KeyError:
        logging.error('No Rollbar settings found')
        return

    rollbar.init(
        api_key,
        environment,
        root=project_root().strpath,
        allow_logging_basic_config=False,
    )

    flask.got_request_exception.connect(wrapped_report_exception, APP)


def app_init_workers():
    """
    Initialize the worker job queue
    """
    from .workers import start_workers
    APP.worker_queue = multiprocessing.Queue()

    try:
        start_workers()
    except Exception:
        rollbar.report_exc_info()
        raise


def app_init_oauth():
    """
    Initialize the OAuth integrations
    """
    from .models.auth import OAuthToken
    if CONFIG.github_enabled:
        from flask_dance.contrib.github import make_github_blueprint

        scope = 'admin:repo_hook,repo,user:email'
        OAUTH_APPS_SCOPES['github'] = \
            OAUTH_APPS_SCOPE_SERIALIZERS['github'](scope)

        blueprint = make_github_blueprint(
            backend=SQLAlchemyBackend(
                OAuthToken,
                DB.session,
                user=current_user,
            ),
            client_id=CONFIG.github_key,
            client_secret=CONFIG.github_secret,
            scope=scope
        )

        APP.register_blueprint(blueprint, url_prefix='/oauth')

        @oauth_authorized.connect_via(blueprint)
        def github_logged_in(blueprint_inner, token):
            import logging
            logging.warning('login hook')
            from flask import flash
            from flask_login import login_user
            from .models.auth import User, UserEmail
            from sqlalchemy.orm.exc import NoResultFound
            if not token:
                flash("Failed to log in with {name}".format(name=blueprint_inner.name))
                return

            # figure out who the user is
            resp = blueprint.session.get("/user")
            if resp.ok:
                logging.warning('resp: %s', resp.json())
                email = resp.json()["email"]
                query = User.query.join(
                    User.primary_email
                ).filter(
                    User.primary_email_str == email,
                )
                try:
                    user = query.one()
                except NoResultFound:
                    email_obj = UserEmail(email=email)
                    user = User(primary_email=email_obj)
                    email_obj.user = user
                    DB.session.add(email_obj)
                    DB.session.add(user)
                    DB.session.commit()

                if not user.active:
                    flash(
                        "User '%s' is inactive" % user.primary_email.email,
                        category='danger',
                    )
                    return

                flash("Successfully signed in with GitHub")
                logging.warning(user)
                login_user(user)
            else:
                msg = "Failed to fetch user info from {name}".format(name=blueprint.name)
                flash(msg, category='danger')


    #if CONFIG.gitlab_enabled:
    #    if 'gitlab' not in OAUTH_APPS:
    #        scope = 'api'
    #        OAUTH_APPS_SCOPES['gitlab'] = \
    #            OAUTH_APPS_SCOPE_SERIALIZERS['gitlab'](scope)
    #        OAUTH_APPS['gitlab'] = OAUTH.remote_app(
    #            'gitlab',
    #            consumer_key=CONFIG.gitlab_key,
    #            consumer_secret=CONFIG.gitlab_secret,
    #            base_url='%s/api/' % CONFIG.gitlab_base_url,
    #            request_token_url=None,
    #            access_token_method='POST',
    #            access_token_url='%s/oauth/token' % CONFIG.gitlab_base_url,
    #            authorize_url='%s/oauth/authorize' % CONFIG.gitlab_base_url,
    #        )


def app_init_handlers():
    """ Initialize event handlers """
    # pylint:disable=unused-variable
    import dockci.handlers


def app_init_api():
    """ Activate the DockCI API """
    # pylint:disable=unused-variable
    import dockci.api


def app_init_views():
    """
    Activate all DockCI views
    """
    # pylint:disable=unused-variable
    import dockci.views
    setup_templates(APP)
