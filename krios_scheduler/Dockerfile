FROM ubuntu:18.04

RUN apt-get update
RUN apt-get install -y software-properties-common apt-utils locales tzdata
RUN apt-get install -y apt-utils
RUN apt-get install -y libssl-dev libcurl4-openssl-dev python3-dev
RUN apt-get install -y curl
RUN apt-get install -y --no-install-recommends python3-pip python3-setuptools
RUN apt-get install -y --no-install-recommends nano
RUN apt-get install -y git

RUN pip3 install --upgrade pip
RUN pip3 install sgp4 kubernetes tcconfig matplotlib

COPY krios_scheduler.py /krios_scheduler.py
COPY k8s.py /k8s.py
COPY utils.py /utils.py
COPY tles.txt /tles.txt

RUN echo "America/New_York" > /etc/timezone
RUN rm -f /etc/localtime
RUN dpkg-reconfigure -f noninteractive tzdata

WORKDIR /
ENTRYPOINT ["python3"]
CMD ["krios_scheduler.py"]
