"""
Users and permissions models
"""

import sqlalchemy

from flask_security import UserMixin, RoleMixin
from flask_security.datastore import SQLAlchemyUserDatastore

from dockci.server import DB

ROLES_USERS = DB.Table(
    'roles_users',
    DB.Column('user_id', DB.Integer(), DB.ForeignKey('user.id'), index=True),
    DB.Column('role_id', DB.Integer(), DB.ForeignKey('role.id')),
)


class DockciUserDatastore(SQLAlchemyUserDatastore):
    """
    Flask-security datastore to add ``UserEmail`` objects for users, and
    get users by all attached emails
    """
    def get_user(self, identifier):
        if self._is_numeric(identifier):
            return self.user_model.query.get(identifier)

        email_obj = UserEmail.query.filter(
            UserEmail.email.ilike(identifier)
        ).first()
        if email_obj is not None:
            return email_obj.user

    def find_user(self, **kwargs):
        email_val = kwargs.pop('email', None)
        base_query = self.user_model.query.filter_by(**kwargs)

        if email_val is None:
            return base_query.first()

        return base_query.join(self.user_model.emails).filter(
            UserEmail.email == email_val,
        ).first()

    def create_user(self, **kwargs):
        user = super(DockciUserDatastore, self).create_user(**kwargs)
        self.put(UserEmail(email=user.email, user=user))
        return user


class Role(DB.Model, RoleMixin):
    """ Role model for granting permissions """
    id = DB.Column(DB.Integer(), primary_key=True)
    name = DB.Column(DB.String(80), unique=True)
    description = DB.Column(DB.String(255))

    def __str__(self):
        return '<{klass}: {name}>'.format(
            klass=self.__class__.__name__,
            name=self.name,
        )


class OAuthToken(DB.Model):  # pylint:disable=no-init
    """ An OAuth token from a service, for a user """
    id = DB.Column(DB.Integer(), primary_key=True)
    service = DB.Column(DB.String(31))
    key = DB.Column(DB.String(80))
    secret = DB.Column(DB.String(80))
    scope = DB.Column(DB.String(255))
    user_id = DB.Column(DB.Integer, DB.ForeignKey('user.id'), index=True)
    user = DB.relationship('User',
                           foreign_keys="OAuthToken.user_id",
                           backref=DB.backref('oauth_tokens', lazy='dynamic'))

    def update_details_from(self, other):
        """
        Update some details from another ``OAuthToken``

        Examples:

        >>> base = OAuthToken(key='basekey')
        >>> other = OAuthToken(key='otherkey')
        >>> other.update_details_from(base)
        >>> other.key
        'basekey'

        >>> base = OAuthToken(secret='basesecret')
        >>> other = OAuthToken(secret='othersecret')
        >>> other.update_details_from(base)
        >>> other.secret
        'basesecret'

        >>> base = OAuthToken(scope='basescope')
        >>> other = OAuthToken(scope='otherscope')
        >>> other.update_details_from(base)
        >>> other.scope
        'basescope'

        >>> base = OAuthToken(key='basekey')
        >>> other = OAuthToken(key='otherkey', secret='sec', scope='sco')
        >>> other.update_details_from(base)
        >>> other.key
        'basekey'
        >>> other.secret
        'sec'
        >>> other.scope
        'sco'

        >>> user1 = User(email=UserEmail(email='1@test.com'))
        >>> user2 = User(email=UserEmail(email='2@test.com'))
        >>> base = OAuthToken(secret='basesec', user=user1)
        >>> other = OAuthToken(secret='othersec', user=user2)
        >>> other.update_details_from(base)
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
          ...
        ValueError: Trying to set token details
        for user <User: 2@test.com... from user <User: 1@test.com...

        >>> other.secret
        'othersec'

        >>> base = OAuthToken(secret='basesec')
        >>> other = OAuthToken(secret='othersec', user=user2)
        >>> other.update_details_from(base)

        >>> base = OAuthToken(secret='basesec', user=user1)
        >>> other = OAuthToken(secret='othersec')
        >>> other.update_details_from(base)
        """
        # Don't allow accidental cross-user updates
        if not (
            self.user is None or
            other.user is None or
            self.user.email == other.user.email
        ):
            raise ValueError(
                "Trying to set token details for user %s from user %s" % (
                    self.user,
                    other.user,
                )
            )

        for attr_name in ('key', 'secret', 'scope'):
            other_val = getattr(other, attr_name)
            if other_val is not None:
                setattr(self, attr_name, other_val)

    def __str__(self):
        return '<{klass}: {provider} for {email}>'.format(
            klass=self.__class__.__name__,
            provider=self.provider,
            email=self.user.email,
        )


class UserEmail(DB.Model):  # pylint:disable=no-init
    """ Email addresses associated with users """
    id = DB.Column(DB.Integer, primary_key=True)
    email = DB.Column(DB.String(255), unique=True, index=True, nullable=False)
    user_id = DB.Column(DB.Integer,
                        DB.ForeignKey('user.id'),
                        index=True,
                        nullable=True)
    user = DB.relationship('User',
                           foreign_keys="UserEmail.user_id",
                           backref=DB.backref('emails', lazy='dynamic'),
                           post_update=True)


class User(DB.Model, UserMixin):  # pylint:disable=no-init
    """ User model for authentication """
    id = DB.Column(DB.Integer, primary_key=True)
    password = DB.Column(DB.String(255))
    active = DB.Column(DB.Boolean())
    confirmed_at = DB.Column(DB.DateTime())
    email = DB.Column(DB.String(255),
                                 DB.ForeignKey('user_email.email'),
                                 index=True,
                                 unique=True,
                                 nullable=False)
    email_obj = DB.relationship('UserEmail',
                                    foreign_keys="User.email",
                                    )
    roles = DB.relationship('Role',
                            secondary=ROLES_USERS,
                            backref=DB.backref('users', lazy='dynamic'))

    def __str__(self):
        return '<{klass}: {email} ({active})>'.format(
            klass=self.__class__.__name__,
            email=self.email,
            active='active' if self.active else 'inactive'
        )

    def oauth_token_for(self, service_name):
        """ Get an OAuth token for a service """
        return self.oauth_tokens.filter_by(
            service=service_name,
        ).order_by(sqlalchemy.desc(OAuthToken.id)).first()


class AuthenticatedRegistry(DB.Model):  # pylint:disable=no-init
    """ Registry that should be authenticated with """
    id = DB.Column(DB.Integer, primary_key=True)
    display_name = DB.Column(DB.String(255), unique=True, nullable=False)
    base_name = DB.Column(DB.String(255),
                          unique=True,
                          index=True,
                          nullable=False,
                          )
    username = DB.Column(DB.String(255))
    password = DB.Column(DB.String(255))
    email = DB.Column(DB.String(255))

    insecure = DB.Column(DB.Boolean, nullable=False, default=False)

    def __str__(self):
        return '<{klass}: {base_name} ({username})>'.format(
            klass=self.__class__.__name__,
            base_name=self.base_name,
            username=self.username,
        )

    def __repr__(self):
        return str(self)

    def __hash__(self):
        return hash(tuple(
            (attr_name, getattr(self, attr_name))
            for attr_name in (
                'id', 'display_name', 'base_name',
                'username', 'password', 'email',
                'insecure',
            )
        ))
