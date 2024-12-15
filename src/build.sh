branch=`git rev-parse --abbrev-ref HEAD`
docker build -t rbrandstaedter/solarflow-control:local .

docker image push rbrandstaedter/solarflow-control:local