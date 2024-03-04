from datetime import datetime
from datetime import timedelta
from functools import reduce
from threading import Timer
import logging
import sys
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
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
        data = {'X': [i for i,v in enumerate(self.values)],
                'y': [v for i,v in enumerate(self.values)]}
        df = pd.DataFrame(data)
        X = df[["X"]]
        y = df["y"]

        X_Train, X_Test, Y_Train, Y_Test = train_test_split(X, y, test_size = 1/3, random_state = 0)

        model = LinearRegression()
        model.fit(X_Train, Y_Train)

        y_pred = model.predict(X_Test) 

        return y_pred

    
def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys.split("."), dictionary)