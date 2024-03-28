branch=`git rev-parse --abbrev-ref HEAD`
docker build -t rbrandstaedter/solarflow-control:$branch .

#docker image push rbrandstaedter/solarflow-control:$branch