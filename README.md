[![Build Status](https://travis-ci.org/RickyCook/DockCI.svg)](https://travis-ci.org/RickyCook/DockCI)[![Requirements Status](https://requires.io/github/RickyCook/DockCI/requirements.svg?branch=master)](https://requires.io/github/RickyCook/DockCI/requirements/?branch=master)[![Tickets in Release](https://badge.waffle.io/rickycook/dockci.svg?label=ready&title=Ready)](http://waffle.io/rickycook/dockci) 

# DockCI
DockCI is still alpha! It's certainly not feature complete.  It's heading towards MVP.

## What is DockCI?
DockCI aims to be one of the missing links in a full Docker CI/CD pipeline.
Currently, most people are testing and deploying containers via build tools
like Jenkins and Bamboo, but these tools were not made for your containerized
new world. The idea of build agents with all your build tools is outdated, the
idea of build steps in your CI config is outdated!

Builds are getting more and more complex, and the old way of approaching CI is
not keeping up. This is what DockCI aims to solve. DockCI's goals are:

1. Configured only via project git repo
1. Minimal custom CI interface
1. Allow effortless, on-demand provisioning of dependant services

Lofty goals! Some might say. But, the tools are already out there. All DockCI
does is pull together some ideas and technologies to help us out.

Sounds great, right? And it will be! But it's not ready yet! Soooooo read on ;D

## Requirements
 - Docker 1.6.0 (others may work)
 - Python 3.4 (may work with 3.x, but untested)

## System setup - Debian Jessie

### Inside a Docker container (recommended)

Registry steps are optional. If you do not want to use a registry at all, you
may disable registry support in the config page when DockCI is running.
Assuming you have Docker set up and running as per instructions at
http://docs.docker.com/installation/debian/ (or similar for your chosen OS),
installing DockCI should be fairly straight forward:

1. Install git `apt-get install git`
1. Change your Docker defaults to listen on the `docker0` interface.
   1. Edit `/etc/default/docker` with your favourite text editor
      `vim /etc/default/docker`
   1. Under the line that has `DOCKER_OPTS` on it, add the following:
      `DOCKER_OPTS="-H tcp://172.17.42.1:2375 -H unix:///var/run/docker.sock --insecure-registry=127.0.0.1:5000"`
      (where `172.17.42.1` is the IP of your docker0 interface. Note, the
      `--insecure-registry` is only required if you will be setting up a
      registry as per later instructions)
   1. Restart Docker `systemctl restart docker.service`
1. Clone the DockCI repo `git clone https://github.com/RickyCook/DockCI.git`
1. Change directory to the DockCI directory, and build a docker container
   `docker build .`
1. Create storage directories `mkdir -p /var/opt/{dockci,docker-registry}`
1. Start a DockCI container: `docker run --detach=true -p 127.0.0.1:5001:5000 -v /var/opt/dockci:/code/data --name dockci <image id>`
1. Start a registry container: `docker run --detach=true -p 127.0.0.1:5000:5000 -v /var/opt/docker-registry:/code/data --name docker-registry registry`
1. You can access your new DockCI at http://127.0.0.1:5001

### Outside of Docker

Follow the previous steps for setup inside a Docker container, except the parts
about Docker, up to the point where you build the container. Note that you will
need one or more Docker daemons running in order to make use of DockCI, and
they may need the `--insecure-registry` daemon option in order to pull and push
correctly (it is assumed that you are able to docker pull/docker push using
your registry).

1. Install extra required packages `apt-get install nodejs npm python3 python3-pip python3-virtualenv`
1. Make the `nodejs` command available as `node` `ln -s $(which nodejs) /usr/bin/node`
1. Change directory to the DockCI directory
1. Install dependencies `make deps`
1. Run DockCI `make run`

## Contributing
If you want to help DockCI see the light of day, pull requests are certainly
accepted! You can probably find something that you can work on in the
[GitHub issues](https://github.com/RickyCook/DockCI/issues). There's also a
[waffle.io project board](https://waffle.io/rickycook/dockci) with issues
ranked (approximately) highest to lowest priority. Just choose something that
isn't already labeled as `in progress`, or already has someone assigned to it
([here's a filter](https://github.com/RickyCook/DockCI/issues?q=is%3Aopen+is%3Aissue+no%3Aassignee+-label%3A%22in+progress%22+)).

The main code base is also really hastily written, so anything that you'd like
to refactor, got nuts! It _really_ needs to be done!

All pull requests must completely pass PEP8 and 10/10 with the pylint config
given. You can check this with `make test` (that's what Travis does too). No
green CI, no merge!

Make sure you update the CHANGELOG.md with any changes you make, including the
pull request number so that we can easily track changes between versions.
You'll obviously need to create the pull request, then update the changelog to
add the PR number.

If you want to have a talk about ideas, project priorities, etc feel free to
drop me an email at mail[at]thatpanda.com.

## Useful issues filters
- [Ticket type unlabeled](https://github.com/RickyCook/DockCI/issues?q=is%3Aissue+is%3Aopen+-label%3Abug+-label%3Aenhancement+-label%3Atask)
- [Things to work on](https://github.com/RickyCook/DockCI/issues?q=is%3Aopen+is%3Aissue+no%3Aassignee+-label%3A%22in+progress%22+)
