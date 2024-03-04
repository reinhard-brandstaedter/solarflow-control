from datetime import datetime
from datetime import timedelta
from functools import reduce
from threading import Timer
import logging
import sys
from sklearn.linear_model import LinearRegression

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class RepeatedTimer:
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.interval   = interval
        self.function   = function
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False

class TimewindowBuffer:
    def __init__(self, minutes: int = 2):
        self.values = []
        self.minutes = minutes

    def __str__(self):
        return "[ " + ",".join([f'{v[1]:>3.1f}' for v in self.values]) + " ]"

    def add(self,value):
        now = datetime.now()
        self.values.append((now,value))
        while True:
            first_ts = self.values[0][0]
            diff = now - first_ts
            if diff.total_seconds()/60 > self.minutes:
                self.values.pop(0)
            else:
                break

    # standard moving average
    def avg(self) -> float:
        n = len(self.values)
        if n == 0: return 0
        return reduce(lambda a,b: a+b, [v[1] for v in self.values])/n
    
    # weighted moving average
    def wavg(self) -> float:
        n = len(self.values)
        if n == 0: return 0
        return reduce(lambda a,b: a+b, [v[1]*(i+1) for i,v in enumerate(self.values)])/((n*(n+1))/2)

    # n^2 weighted moving average
    def qwavg(self) -> float:
        n = len(self.values)
        if n == 0: return 0
        return reduce(lambda a,b: a+b, [v[1]*((i+1)*(i+1)) for i,v in enumerate(self.values)])/((n*(n+1)*(2*n+1))/6)
    
    def clear(self):
        self.values = []

    def predict(self) -> []:
        x = [list(map(lambda i: i[0], self.values))]
        y = [list(map(lambda i: i[1], self.values))]

        model = LinearRegression()
        model.fit(x, y)

        x_predict = [(x[-1:]+timedelta(seconds=10)).strftime("%H:%M:%S"),(x[-1:]+timedelta(seconds=20)).strftime("%H:%M:%S")]  # put the dates of which you want to predict kwh here
        y_predict = model.predict(x_predict)

        return y_predict

    
def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys.split("."), dictionary)