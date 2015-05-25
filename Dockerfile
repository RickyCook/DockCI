FROM debian:jessie

EXPOSE 5000
ENTRYPOINT ["/usr/bin/make"]
CMD ["run"]

ENV DEBIAN_FRONTEND noninteractive
RUN apt-get update && apt-get install -y aria2 && \
    aria2c -d /tmp "https://raw.githubusercontent.com/RickyCook/minimal-apt-fast/5750510/install.sh" && \
    APT_FAST_VERSION=v1.7.6 NO_APT_UPDATE=y sh -e /tmp/install.sh && \
    rm /tmp/install.sh && \
    apt-fast install -y \
        git nodejs npm locales \
        python3 python3-pip python3-virtualenv
RUN ln -s $(which nodejs) /usr/bin/node

RUN echo 'en_AU.UTF-8 UTF-8' > /etc/locale.gen && locale-gen && update-locale LANG=en_AU.UTF-8
ENV LANG en_AU.UTF-8

RUN mkdir -p /code/data
WORKDIR /code
ADD Makefile /code/Makefile

ADD package.json /code/package.json
ADD bower.json /code/bower.json
RUN make htmldeps collectstatic

ADD requirements.txt /code/requirements.txt
RUN make pythondeps

ADD . /code
