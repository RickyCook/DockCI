collectstatic: htmldeps
	mkdir -p dockci/static/lib/css
	mkdir -p dockci/static/lib/fonts
	mkdir -p dockci/static/lib/js

	cp bower_components/bootstrap/dist/css/bootstrap.min.css dockci/static/lib/css
	cp bower_components/bootstrap-material-design/dist/css/material.min.css dockci/static/lib/css
	cp bower_components/bootstrap-material-design/dist/css/material-wfont.min.css dockci/static/lib/css
	cp bower_components/bootstrap-material-design/dist/css/ripples.min.css dockci/static/lib/css

	cp bower_components/bootstrap-material-design/dist/fonts/Material-Design-Icons.woff dockci/static/lib/fonts
	cp bower_components/bootstrap-material-design/dist/fonts/Material-Design-Icons.ttf dockci/static/lib/fonts

	cp bower_components/ansi_up/ansi_up.js dockci/static/lib/js
	cp bower_components/blueimp-md5/js/md5.min.js dockci/static/lib/js
	cp bower_components/bootstrap/js/dropdown.js dockci/static/lib/js
	cp bower_components/bootstrap/js/modal.js dockci/static/lib/js
	cp bower_components/bootstrap/js/tab.js dockci/static/lib/js
	cp bower_components/bootstrap/js/transition.js dockci/static/lib/js
	cp bower_components/bootstrap-material-design/dist/js/material.min.js dockci/static/lib/js
	cp bower_components/bootstrap-material-design/dist/js/ripples.min.js dockci/static/lib/js
	cp bower_components/jquery/dist/jquery.min.js dockci/static/lib/js
	cp bower_components/uri.js/src/jquery.URI.min.js dockci/static/lib/js
	cp bower_components/uri.js/src/URI.min.js dockci/static/lib/js

htmldeps:
	npm install
	node_modules/bower/bin/bower --allow-root install
pythondeps:
	python3.4 -m virtualenv -p $(shell which python3.4) python_env
	python_env/bin/pip install -r requirements.txt
testdeps:
	python_env/bin/pip install -r test-requirements.txt
deps: htmldeps pythondeps collectstatic

styletest:  # don't install deps
	python_env/bin/pep8 dockci
	python_env/bin/pylint --rcfile pylint.conf dockci
unittest:
	python_env/bin/pytest -vv -t test
test: testdeps styletest unittest

# Container commands
ci: test
migrate:
	@python_env/bin/python -m dockci.migrations.run
run: migrate
	@python_env/bin/gunicorn --workers 20 --timeout 0 --bind 0.0.0.0:5000 --preload wsgi
sh:
	@sh

.PHONY: ci collectstatic htmldeps pythondeps testdeps deps run styletest test
