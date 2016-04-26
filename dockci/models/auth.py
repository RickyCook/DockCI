"""
Users and permissions models
"""

import sqlalchemy

from flask_dance.consumer.backend.sqla import OAuthConsumerMixin
from flask_security import UserMixin, RoleMixin
from sqlalchemy.ext.hybrid import hybrid_property

from dockci.server import DB

ROLES_USERS = DB.Table(
    'roles_users',
    DB.Column('user_id', DB.Integer(), DB.ForeignKey('user.id'), index=True),
    DB.Column('role_id', DB.Integer(), DB.ForeignKey('role.id')),
)


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


class OAuthToken(DB.Model, OAuthConsumerMixin):  # pylint:disable=no-init
    """ An OAuth token from a service, for a user """
    id = DB.Column(DB.Integer(), primary_key=True)
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

        >>> user1 = User(primary_email=UserEmail(email='1@test.com'))
        >>> user2 = User(primary_email=UserEmail(email='2@test.com'))
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
            self.user.primary_email == other.user.primary_email
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
            email=self.user.primary_email.email,
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
    primary_email_id = DB.Column(DB.Integer,
                                 DB.ForeignKey('user_email.id'),
                                 index=True,
                                 nullable=False)
    primary_email = DB.relationship('UserEmail',
                                    foreign_keys="User.primary_email_id")
    roles = DB.relationship('Role',
                            secondary=ROLES_USERS,
                            backref=DB.backref('users', lazy='dynamic'))

    @hybrid_property
    def primary_email_str(self):  # pylint:disable=method-hidden
        """ Get ``primary_email.email`` """
        return self.primary_email.email

    @primary_email_str.setter
    def primary_email_str(self, value):  # pylint:disable=method-hidden
        """
        Setter to create new ``UserEmail`` and set ``primary_email`` from a
        string
        """
        email = UserEmail(email=value, user=self)
        DB.session.add(email)

    @primary_email_str.expression
    def primary_email_str(cls):  # noqa pylint:disable=no-self-argument,no-self-use,method-hidden
        """
        Unwrap the ``UserEmail`` from ``primary_email`` for easy querying
        """
        return UserEmail.email

    #@hybrid_property
    #def email(self):
    #    """ For Flask-Security; See ``primary_email`` """
    #    return self.primary_email_str

    #@email.setter
    #def email(self, value):
    #    """ For Flask-Security; See ``primary_email`` setter """
    #    self.primary_email_str = value

    #@email.expression
    #def email(cls):  # pylint:disable=no-self-argument
    #    """ For Flask-Security; See ``primary_email`` expression """
    #    return cls.primary_email_str

    def __str__(self):
        return '<{klass}: {primary_email} ({active})>'.format(
            klass=self.__class__.__name__,
            primary_email=self.primary_email.email,
            active='active' if self.active else 'inactive'
        )

    def oauth_token_for(self, service_name):
        """ Get an OAuth token for a service """
        return self.oauth_tokens.filter_by(
            provider=service_name,
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
