# Django settings for django_hello project used on Debian systems.

import django
import os
import re
import simplejson

from lava_server.settings.production import *
from lava_server.settings.config_file import ConfigFile

from lava_server.settings.secret_key import get_secret_key


# Load the setting file and add the variables to the current context
try:
    with open("/etc/lava-server/settings.conf", "r") as f_conf:
        for (k, v) in simplejson.load(f_conf).items():
            globals()[k] = v
except (AttributeError, ValueError):
    pass

# Fix mount point
# Remove the leading slash and keep only one trailing slash
MOUNT_POINT = (MOUNT_POINT.rstrip("/") + "/").lstrip("/")

# Fix ADMINS and MANAGERS variables
# In Django < 1.9, this is a tuple of tuples
# In Django >= 1.9 this is a list of tuples
# See https://docs.djangoproject.com/en/1.8/ref/settings/#admins
# and https://docs.djangoproject.com/en/1.9/ref/settings/#admins
if django.VERSION < (1, 9):
    ADMINS = tuple(tuple(v) for v in ADMINS)
    MANAGERS = tuple(tuple(v) for v in MANAGERS)
else:
    ADMINS = [tuple(v) for v in ADMINS]
    MANAGERS = [tuple(v) for v in MANAGERS]

# Load default database from distro integration
config = ConfigFile.load("/etc/lava-server/instance.conf")
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql_psycopg2",
                         "NAME": getattr(config, "LAVA_DB_NAME", ""),
                         "USER": getattr(config, "LAVA_DB_USER", ""),
                         "PASSWORD": getattr(config, "LAVA_DB_PASSWORD", ""),
                         "HOST": getattr(config, "LAVA_DB_SERVER", "127.0.0.1"),
                         "PORT": getattr(config, "LAVA_DB_PORT", ""), }}

# Load secret key from distro integration
SECRET_KEY = get_secret_key("/etc/lava-server/secret_key.conf")

# LDAP authentication config
if AUTH_LDAP_SERVER_URI:
    INSTALLED_APPS.append('ldap')
    INSTALLED_APPS.append('django_auth_ldap')
    import ldap
    from django_auth_ldap.config import (LDAPSearch, LDAPSearchUnion)

    def get_ldap_group_types():
        """Return a list of all LDAP group types supported by django_auth_ldap module"""
        import django_auth_ldap.config
        import inspect
        types = []
        for name, obj in inspect.getmembers(django_auth_ldap.config):
            if inspect.isclass(obj) and name.endswith('Type'):
                types.append(name)

        return types

    AUTHENTICATION_BACKENDS = ['django_auth_ldap.backend.LDAPBackend',
                               'django.contrib.auth.backends.ModelBackend']

    # Available variables: AUTH_LDAP_BIND_DN, AUTH_LDAP_BIND_PASSWORD,
    # AUTH_LDAP_USER_DN_TEMPLATE AUTH_LDAP_USER_ATTR_MAP

    if AUTH_LDAP_USER_SEARCH:
        AUTH_LDAP_USER_SEARCH = eval(AUTH_LDAP_USER_SEARCH)
        # AUTH_LDAP_USER_SEARCH and AUTH_LDAP_USER_DN_TEMPLATE are mutually
        # exclusive, hence,
        AUTH_LDAP_USER_DN_TEMPLATE = None

    if AUTH_LDAP_GROUP_SEARCH:
        AUTH_LDAP_GROUP_SEARCH = eval(AUTH_LDAP_GROUP_SEARCH)

    if AUTH_LDAP_GROUP_TYPE:
        group_type = AUTH_LDAP_GROUP_TYPE
        # strip params from group type to get the class name
        group_class = group_type.split('(', 1)[0]
        group_types = get_ldap_group_types()
        if group_class in group_types:
            exec('from django_auth_ldap.config import ' + group_class)
            AUTH_LDAP_GROUP_TYPE = eval(group_type)

elif AUTH_DEBIAN_SSO:
    MIDDLEWARE_CLASSES.append('lava_server.debian_sso.DebianSsoUserMiddleware')
    AUTHENTICATION_BACKENDS.append('lava_server.debian_sso.DebianSsoUserBackend')

if USE_DEBUG_TOOLBAR:
    INSTALLED_APPS.append('debug_toolbar')
    MIDDLEWARE_CLASSES = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE_CLASSES
    INTERNAL_IPS.extend(['127.0.0.1', '::1'])

# List of compiled regular expression objects representing User-Agent strings
# that are not allowed to visit any page, systemwide. Use this for bad
# robots/crawlers
DISALLOWED_USER_AGENTS = [re.compile(r'%s' % reg, re.IGNORECASE) for reg in DISALLOWED_USER_AGENTS]

# Set instance name
if os.path.exists("/etc/lava-server/instance.conf"):
    instance_config = ConfigFile.load("/etc/lava-server/instance.conf")
    instance_name = instance_config.LAVA_INSTANCE

INSTANCE_NAME = globals().get("INSTANCE_NAME", instance_name)

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse'
        }
    },
    'formatters': {
        'lava': {
            'format': '%(levelname)s %(asctime)s %(module)s %(message)s'
        }
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'lava'
        },
        'logfile': {
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': DJANGO_LOGFILE,
            'formatter': 'lava'
        }
    },
    'loggers': {
        'django': {
            'handlers': ['logfile'],
            # DEBUG outputs all SQL statements
            'level': 'ERROR',
            'propagate': True,
        },
        'django_auth_ldap': {
            'handlers': ['logfile'],
            'level': 'INFO',
            'propagate': True,
        },
        'lava_results_app': {
            'handlers': ['logfile'],
            'level': 'INFO',
            'propagate': True,
        },
        'lava_scheduler_app': {
            'handlers': ['logfile'],
            'level': 'INFO',
            'propagate': True,
        },
        'publisher': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        }
    }
}

# Add template caching
if USE_TEMPLATE_CACHE:
    TEMPLATES[0]['OPTIONS']['loaders'] = [('django.template.loaders.cached.Loader',
                                           TEMPLATES[0]['OPTIONS']['loaders'])]
