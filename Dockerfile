FROM debian:jessie

EXPOSE 5000
ENTRYPOINT ["/usr/bin/make"]
CMD ["run"]

ENV DEBIAN_FRONTEND noninteractive
RUN apt-get update && apt-get install -y \
    git nodejs npm \
    python3 python3-pip virtualenv
RUN ln -s $(which nodejs) /usr/bin/node

RUN mkdir -p /code/data
WORKDIR /code
ADD Makefile /code/Makefile

ADD package.json /code/package.json
ADD bower.json /code/bower.json
RUN make htmldeps

ADD requirements.txt /code/requirements.txt
RUN make pythondeps

ADD . /code
