#!/bin/bash
function collectstatic {
    mkdir -p dockci/static/lib/css
    mkdir -p dockci/static/lib/fonts
    mkdir -p dockci/static/lib/js
    ./manage_collectstatic.sh; exit $?
}
function htmldeps {
    npm install
    node_modules/bower/bin/bower --allow-root install
}
function env_create {
    python3 -m virtualenv -p $(which python3) python_env
}
function env_install_reqs {
    [[ -e python_env ]] || env_create
    python_env/bin/pip install --use-wheel --no-index --find-links=wheelhouse -r "$1"
}
function pythondeps {
    env_install_reqs requirements.txt
    env_install_reqs test-requirements.txt
}
function styletest {
    python_env/bin/pep8 dockci
    python_env/bin/pylint --rcfile pylint.conf dockci
}
function unittest {
    export PYTHONPATH=$(pwd)
    python_env/bin/py.test -vv tests
}
function tests {
    styletest
    unittest
}
function ci {
    tests
}
function migrate {
    python_env/bin/python -m dockci.migrations.run
}
function run {
    migrate
    python_env/bin/gunicorn --workers 20 --timeout 0 --bind 0.0.0.0:5000 --preload wsgi
}
function shell {
    /bin/bash
}

case $1 in
    collectstatic) collectstatic ;;
    pythondeps) pythondeps ;;
    ci) ci ;;
    shell) shell ;;
    migrate) migrate ;;
    run) run ;;
    *)
        echo "Unknown command '$1'" >&2
        exit 1
esac
